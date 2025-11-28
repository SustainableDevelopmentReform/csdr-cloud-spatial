import asyncio
import json
import logging
import os
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import HTTPStore, LocalStore, S3Store, from_url
from pyarrow import ArrowInvalid

# We support three types of stores: 
# 1. S3Store for s3:// URLs
# 2. HTTPStore for http:// and https:// URLs
# 3. LocalStore for local file paths starting with / or ./

# Store always includes prefix for all store types.

def exists(store: HTTPStore | S3Store | LocalStore, file_name: str) -> bool:
    # store includes prefix, but not file_name.
    try:
        store.head(file_name) # Try get metadata
    except FileNotFoundError:
        return False
    return True


def get_file_info(store: HTTPStore | S3Store | LocalStore, file_name: str) -> dict[str, Any]:
    # store includes prefix, but not file_name
    info = store.head(file_name)
    return {
        "size": info["size"],
        "e_tag": info["e_tag"],
        "last_modified": info.get("last_modified", None),
    }


def write_json(
    store: HTTPStore | S3Store | LocalStore, file_name: str, data: dict[str, Any]
) -> None:
    # All store types have put method
    store.put(
        file_name,
        json.dumps(data, indent=2).encode("utf-8"),
        attributes={"Content-Type": "application/json"},
    )


def get_store_with_prefix_from_url(
    url: str, mkdir: bool = True, **kwargs: dict
) -> HTTPStore | S3Store | LocalStore:
    # https://developmentseed.org/obstore/latest/api/store/#obstore.store.from_url
    if url.startswith("s3://") or url.startswith("http://") or url.startswith("https://") or url.startswith("file://"):
        # S3, Http, and file URLs (that start with "file://")
        return from_url(url, credential_provider=Boto3CredentialProvider(), mkdir=mkdir, **kwargs)
    else:
        # File URLs that don't start with "file://"
        return from_url(f"file://{os.path.abspath(url)}", mkdir=mkdir, **kwargs)

# test = from_url("file:///Users/wj/Projects/csdr/csdr-cloud-spatial/README.md")
# test = from_url("s3://bucket-name/path/to/blob.txt")
# test = from_url("https://files.auspatious.com/#share/tide_models_clipped_indonesia.zip")
# HTTPStore has url prop
# S3Store has prefix prop. Also has config.bucket prop
# LocalStore has prefix prop

def get_url_from_store(store: HTTPStore | S3Store | LocalStore) -> str:
    if type(store) is HTTPStore:
        return store.url
    elif type(store) is S3Store:
        return f"s3://{store.config['bucket']}/{store.prefix}"
    elif type(store) is LocalStore:
        return store.prefix
    else:
        raise ValueError(f"Unsupported store type: {type(store)}")


def get_file_name_from_url(url: str) -> str:
    # Get last "/" and return everything after it as the file name
    return urlparse(url).path.split("/")[-1]


def read_dict(store: S3Store | LocalStore, file_name: str) -> dict[str, Any]:
    with BytesIO(store.get(file_name).bytes()) as buffer:
        try:
            json_dict = json.load(buffer)
            return json_dict
        except Exception as e:
            logging.error(f"Failed to read dict from {file_name} with exception {e}", exc_info=True)
            raise


def read_geospatial_file(url: str, **kwargs: dict) -> gpd.GeoDataFrame:
    store = get_store_with_prefix_from_url(url)
    prefix_filename = get_file_name_from_url(url)

    with BytesIO(store.get(prefix_filename).bytes()) as buffer:
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
                raise


async def get_stac_item_dicts_from_store(
    store: S3Store | LocalStore | HTTPStore
) -> list[dict[str, Any]]:
    list_of_stac_files = []

    logging.info("Listing STAC items in store recursively")

    for i, batch in enumerate(store.list(chunk_size=1000)): # default chunk_size is 50 which is very low just to list files
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


def write_gdf_to_parquet(
    gdf: gpd.GeoDataFrame, store: S3Store | LocalStore | HTTPStore, file_name: str
) -> None:
    # Write GeoDataFrame to a GeoParquet file in memory
    with BytesIO() as parquet_buffer:
        gdf.to_parquet(parquet_buffer, engine="pyarrow")
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        store.put(file_name, parquet_buffer.getvalue())
