import asyncio
import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd
from loguru import logger
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import HTTPStore, LocalStore, S3Store
from pyarrow import ArrowInvalid


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


def get_store_for_url(
    url: str, mkdir: bool = True, **kwargs: dict
) -> HTTPStore | S3Store | LocalStore:
    # This function allows the code to work seamlessly with local files, S3 buckets, or HTTP endpoints by abstracting the storage backend.
    if url.startswith("s3://"):
        s3_url = urlparse(url)
        bucket = s3_url.netloc
        return S3Store(bucket, credential_provider=Boto3CredentialProvider(), **kwargs)
    elif url.startswith("http://") or url.startswith("https://"):
        parsed = urlparse(url)
        return HTTPStore(f"{parsed.scheme}://{parsed.netloc}", **kwargs)
    else:
        the_path = Path(url)
        # Ensure the directory exists
        if mkdir:
            if the_path.suffix:  # It's a file path
                the_path.parent.mkdir(parents=True, exist_ok=True)
            else:  # It's a directory path
                the_path.mkdir(parents=True, exist_ok=True)

        # LocalStore expects the directory, not a file
        path_prefix = the_path if not the_path.suffix else the_path.parent
        return LocalStore(prefix=path_prefix, **kwargs)


def get_file_name_from_url(url: str) -> str:
    parsed_url = urlparse(url)
    file_name = os.path.basename(parsed_url.path)
    if "." not in file_name:
        file_name = None

    if file_name is not None:
        file_name = file_name.lstrip("/")

    return file_name


# A prefix is the path of a path that excludes the provider (S3, HTTP, Local), and the file name and extension.
# E.g. the s3://my-bucket/path/to/myfile.geojson has prefix path/to. Locally, /data/files/myfile.geojson has prefix data/files.
def get_s3_prefix(url: str) -> str | None:
    # Should we validate the input? E.g. Check that the url starts with 's3://'. Otherwise error because it is not an S3 path.
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
def prepend_prefix_if_s3_store(store: HTTPStore | S3Store | LocalStore, url: str, filename: str) -> str:
    if type(store) is S3Store:
        # S3Store needs the full path
        s3_prefix = get_s3_prefix(url)
        if s3_prefix is not None:
            filename = f"{s3_prefix}/{filename}"
    return filename


def get_dataset_name_from_url(
    store: HTTPStore | S3Store | LocalStore, url: str, keep_path: bool = True
) -> str:
    dataset_name = get_file_name_from_url(url)
    if dataset_name is None:
        raise ValueError(f"Could not determine dataset name from URL: {url}")

    if keep_path:
        dataset_name = prepend_prefix_if_s3_store(store, url, dataset_name)
    dataset_name = dataset_name.lstrip("/").rstrip("/")

    return dataset_name


def get_url_from_store_filename(
    store: HTTPStore | S3Store | LocalStore, filename: str
) -> str:
    if type(store) is HTTPStore:
        return f"{store.url.rstrip('/')}/{filename.lstrip('/')}"
    elif type(store) is S3Store:
        return f"s3://{store.config['bucket']}/{filename.lstrip('/')}"
    elif type(store) is LocalStore:
        return f"{store.prefix}/{filename}"
    else:
        raise ValueError(f"Unsupported store type: {type(store)}")


def read_dict(store: S3Store | LocalStore, path: str) -> dict[str, Any]:
    with BytesIO(store.get(path).bytes()) as buffer:
        try:
            json_dict = json.load(buffer)
            return json_dict
        except Exception as e:
            logger.exception(f"Failed to read dict from {path} with exception {e}")


def read_geospatial_file(url: str, **kwargs: dict) -> gpd.GeoDataFrame:
    store = get_store_for_url(url)
    path = get_dataset_name_from_url(store, url)

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
                logger.exception(
                    f"Failed to read geospatial file from {url} with exception {e}"
                )


async def get_stac_item_dicts_from_store(
    store: S3Store, s3_prefix: str | None = None
) -> list[dict[str, Any]]:
    list_of_stac_files = []

    for batch in store.list(s3_prefix):
        for stac_file in batch:
            if stac_file["path"].endswith(".stac-item.json"):
                list_of_stac_files.append(stac_file)

    async def _fetch_item(store: S3Store, stac_file: dict) -> dict:
        obj = await store.get_async(stac_file["path"])
        data = BytesIO(obj.bytes())
        return json.load(data)

    return await asyncio.gather(
        *(_fetch_item(store, stac_file) for stac_file in list_of_stac_files)
    )


def write_gdf_to_parquet(
    gdf: gpd.GeoDataFrame, store: S3Store | LocalStore, filename: str
) -> None:
    # Write GeoDataFrame to a GeoParquet file in memory
    with BytesIO() as parquet_buffer:
        gdf.to_parquet(parquet_buffer, engine="pyarrow")
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        store.put(filename, parquet_buffer.getvalue())
