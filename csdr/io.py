import asyncio
import json
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import HTTPStore, LocalStore, S3Store
from pyarrow import ArrowInvalid

# Here is a suite of functions to handle different storage backends (local filesystem, S3, HTTP) using obstore.
# get_store_from_url: Given a URL, return the appropriate obstore store (S3Store, HTTPStore, LocalStore).
# get_prefix_from_url: Extract the prefix (path) from a given URL.
# get_file_name_from_url: Extract the file name from a given URL.
# Together these three functions extract mutually exclusive components of a URL for file storage.

# We support three types of stores: 
# 1. S3Store for s3:// URLs
# 2. HTTPStore for http:// and https:// URLs
# 3. LocalStore for local file paths starting with / or ./

# They have the following characteristics:
# URL: https://test.com/path/to/blob.txt
# Store: HTTPStore with base URL https://test.com
# Prefix: path/to
# Filename: blob.txt
# STORE DOESN'T CONTAIN PREFIX

# URL: /path/to/blob.txt
# Store: LocalStore with prefix /path/to
# Prefix: /path/to
# Filename: blob.txt
# STORE DOES CONTAIN PREFIX !!!!!!!!!!!!!!!!!!!!!

# URL: s3://bucket-name/path/to/blob.txt
# Store: S3Store with bucket bucket-name
# Prefix: path/to
# Filename: blob.txt
# STORE DOESN'T CONTAIN PREFIX

# Standard naming:
# 'url' is the full url including store, prefix, and filename.
# 'store' is just the store for http and s3, while it is the store and prefix for local.
# 'path' is the filename and prefix for http and s3, but it is just the filename for local.

def exists(store: HTTPStore | S3Store | LocalStore, path: str) -> bool:
    try:
        store.head(path)
    except FileNotFoundError:
        return False
    return True


def get_file_info(store: HTTPStore | S3Store | LocalStore, path: str) -> dict[str, Any]:
    info = store.head(path)
    return {
        "size": info["size"],
        "e_tag": info["e_tag"],
        "last_modified": info.get("last_modified", None),
    }


def write_json(
    store: HTTPStore | S3Store | LocalStore, path: str, data: dict[str, Any]
) -> None:
    if type(store) is HTTPStore:
        raise ValueError("Cannot write to HTTPStore")
    elif type(store) is LocalStore:
        # TODO: validate that LocalStore does not have put. I think it does https://developmentseed.org/obstore/latest/api/store/local/#obstore.store.LocalStore.put
        # Use put if possible to streamline code
        full_path = Path(store.prefix) / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    else:
        # S3Store has put
        store.put(
            path,
            json.dumps(data, indent=2).encode("utf-8"),
            attributes={"Content-Type": "application/json"},
        )

# TODO: This function is returning the prefix as well (maybe for just some store types like local?). It should just return the store without the prefix. Another function return just the prefix.
# Actually local kind of needs the prefix to know where to read from/write to. This is an incosistency with S3 and HTTP stores that we need to handle.
# Need to define where the root of the local store is. Maybe always use the actual computer root "/"? Otherwise could use the current working directory but either way we need to be consistent.
def get_store_from_url(
    url: str, mkdir: bool = True, **kwargs: dict
) -> HTTPStore | S3Store | LocalStore:
    # This function allows the code to work seamlessly with local files, S3 buckets, or HTTP S3 endpoints by abstracting the storage backend.
    # TODO: what is kwargs? The only time something else is passed in is region="us-west-2" for cli_dataset_seagrass.py. This should be used for S3, not for local.
    # S3 Store includes bucket but not prefix
    if url.startswith("s3://"):
        # S3 URL
        s3_url = urlparse(url)
        bucket = s3_url.netloc
        return S3Store(bucket, credential_provider=Boto3CredentialProvider(), **kwargs)
    # Http Store includes base url, but not prefix
    elif url.startswith("http://") or url.startswith("https://"):
        # HTTP URL
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        return HTTPStore(base_url, **kwargs) # this adds a trailing slash...
    # Local Store includes prefix
    elif url.startswith("/") or url.startswith("./"):
        # Local path
        local_path = Path(url).parent if mkdir else Path(url)
        # Create local directory if it doesn't exist and mkdir is True
        if mkdir:
            local_path.mkdir(parents=True, exist_ok=True)
        return LocalStore(prefix=local_path, **kwargs) # The local store should includes the store and the prefix by design.
        # return LocalStore(prefix="/", **kwargs) # Always use the absolute root directory as the LocalStore prefix
    else:
        raise ValueError(f"Unsupported store type from URL '{url}'")


# TODO: Make a standardised function to get the prefix from a URL.
# The prefix is everthing between the Store and the file name e.g. "prefix/to" from "s3://bucket-name/prefix/to/file.txt" or "/prefix/to/file.txt"
# No leading or trailing slashes.
# TODO: Use this function. It is not used anywhere currently.
def get_prefix_from_url(url: str) -> str | None:
    # TODO: Write a robust function to get the prefix from a URL.
    store = get_store_from_url(url)
    file_name = get_file_name_from_url(url)
    prefix = url.replace(file_name, "").replace(store, "").lstrip("/").rstrip("/") # This probably doesn't work but is the idea.
    # if type(store) is S3Store:
    #     dataset_name = prepend_prefix_if_s3_store(store, url, dataset_name)
    #     prefix = url.replace(dataset_name, "").lstrip("s3://").rstrip("/")
    # elif type(store) is HTTPStore or type(store) is LocalStore:
    #     parsed = urlparse(url)
    #     # For local paths, parsed.path may start with '/' (absolute) or './' (relative)
    #     dataset_name = parsed.path.lstrip("/.")
    # else:
    #     raise ValueError(f"Unsupported store type: {type(store)}")
    return prefix


# Does not return the path, just the file name and file extension e.g. "file.txt" from "s3://bucket-name/prefix/to/file.txt" or "/prefix/to/file.txt"
def get_file_name_from_url(url: str) -> str:
    parsed_url = urlparse(url)
    file_name = os.path.basename(parsed_url.path)
    if "." not in file_name:
        file_name = None

    if file_name is not None:
        file_name = file_name.lstrip("/")
    else:
        raise ValueError(f"Could not determine file name from URL: {url}")

    return file_name


# get_s3_prefix is needed because local and http stores include the prefixes so they are not needed to be extracted separately.
# TODO: extend this function to support https too?
def get_s3_prefix(url: str) -> str | None:
    # Should we validate the input? E.g. Check that the url starts with 's3://'. Otherwise error because it is not an S3 path.
    store = get_store_from_url(url)
    if type(store) is not S3Store:
        raise ValueError(f"get_s3_prefix only works for S3 URLs. Provided URL: {url}")
    
    s3_url = urlparse(url)
    file_name = get_file_name_from_url(url)

    s3_prefix = None
    if file_name is not None and s3_url.path.endswith(file_name):
        # Remove file name and extension from path if present. Remove leading and trailing slashes.
        s3_prefix = s3_url.path.lstrip("/").replace(file_name, "").rstrip("/")
    else:
        # Else just remove leading and trailing slashes
        s3_prefix = s3_url.path.lstrip("/").rstrip("/")

    # Handle case where prefix is empty string
    if s3_prefix == "":
        s3_prefix = None

    # Remove any leading or trailing slashes
    if s3_prefix is not None:
        s3_prefix = s3_prefix.lstrip("/").rstrip("/")

    return s3_prefix


# Returns prefix + filename for S3, else just filename
def prepend_prefix_if_s3_store(store: HTTPStore | S3Store | LocalStore, url: str, path: str) -> str:
    if type(store) is S3Store:
        # S3Store needs the full path
        s3_prefix = get_s3_prefix(url)
        if s3_prefix is not None:
            path = f"{s3_prefix}/{path}"
    return path # path is the original for local and http, but includes the prefix for s3


# TODO: Should prefix and filename be two separate arguments?
def make_url_from_store_prefix_filename(
    store: HTTPStore | S3Store | LocalStore, path: str
) -> str:
    # This function only needs 2 parameters because:
    # Http and Local stores include the prefix in the store itself.
    # S3 stores do not include the prefix, but we have added it to the filename.
    # So either way, we will end up with the full url.
    if type(store) is HTTPStore:
        return f"{store.url.rstrip('/')}/{path.lstrip('/')}"
    elif type(store) is S3Store:
        return f"s3://{store.config['bucket']}/{path.lstrip('/')}"
    elif type(store) is LocalStore:
        # The Local store is expected to include the prefix. It would be good if filename was a seperate param.
        return store.prefix.joinpath(get_file_name_from_url(path)).as_posix()
    else:
        raise ValueError(f"Unsupported store type: {type(store)}")


def read_dict(store: S3Store | LocalStore, path: str) -> dict[str, Any]:
    with BytesIO(store.get(path).bytes()) as buffer:
        try:
            json_dict = json.load(buffer)
            return json_dict
        except Exception as e:
            logging.error(f"Failed to read dict from {path} with exception {e}", exc_info=True)
            


def read_geospatial_file(url: str, **kwargs: dict) -> gpd.GeoDataFrame:
    store = get_store_from_url(url)
    path = get_file_name_from_url(store, url)

    with BytesIO(store.get(path).bytes()) as buffer:
        try:
            # TODO: Make it read more things, not just parquet
            gdf = gpd.read_parquet(buffer, **kwargs)
            return gdf
        except ValueError:
            # Try loading as a regular parquet file
            buffer.seek(0)
            return pd.read_parquet(buffer, **kwargs)
        except ArrowInvalid:
            # Try loading as generic file
            buffer.seek(0)
            try:
                gdf = gpd.read_file(buffer, **kwargs)
                return gdf
            except Exception as e:
                logging.error(
                    f"Failed to read geospatial file from {url} with exception {e}", exc_info=True
                )


# This function only supports S3Store. Not local or Http.
async def get_stac_item_dicts_from_store(
    store: S3Store | LocalStore | HTTPStore, s3_prefix: str | None = None
) -> list[dict[str, Any]]:
    list_of_stac_files = []

    logging.info("Listing STAC items in store recursively")
    
    for i, batch in enumerate(store.list(s3_prefix, chunk_size=1000)): # default chunk_size is 50 which is very low just to list files
        logging.info(f"Batch number {i} of {len(batch)} files...")
        for stac_file in batch:
            if stac_file["path"].endswith(".stac-item.json"):
                list_of_stac_files.append(stac_file)

    logging.info(f"Found {len(list_of_stac_files)} STAC items.")

    # Use semaphore to limit concurrent requests to prevent S3 from timing out. Otherwise it would request all concurrently and sometimes time out.
    semaphore = asyncio.Semaphore(1000)
    async def _fetch_item(store: S3Store | LocalStore | HTTPStore, stac_file: dict) -> dict:
        async with semaphore:
            obj = await store.get_async(stac_file["path"])
            data = BytesIO(obj.bytes())
            return json.load(data)
    return await asyncio.gather(*(_fetch_item(store, stac_file) for stac_file in list_of_stac_files))


# This function only supports S3Store and LocalStore. Not Http.
def write_gdf_to_parquet(
    gdf: gpd.GeoDataFrame, store: S3Store | LocalStore, path: str
) -> None:
    # Write GeoDataFrame to a GeoParquet file in memory
    with BytesIO() as parquet_buffer:
        gdf.to_parquet(parquet_buffer, engine="pyarrow")
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        store.put(path, parquet_buffer.getvalue())
