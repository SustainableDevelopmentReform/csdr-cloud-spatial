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
from obstore.store import HTTPStore, LocalStore, ObjectStore, S3Store, from_url
from pyarrow import ArrowInvalid

# We support three types of stores: 
# 1. S3Store for s3:// URLs
# 2. HTTPStore for http:// and https:// URLs
# 3. LocalStore for local file paths starting with / or ./

# Store always includes prefix for all store types.

# exists just works for files, not directories.
def exists(store: ObjectStore, file_name: str) -> bool:
    # store includes prefix, but not file_name.
    try:
        store.head(file_name) # Try get metadata
    except FileNotFoundError:
        return False
    return True


def get_file_info(store: ObjectStore, file_name: str) -> dict[str, Any]:
    # store includes prefix, but not file_name
    info = store.head(file_name)
    return {
        "size": info["size"],
        "e_tag": info["e_tag"],
        "last_modified": info.get("last_modified", None),
    }


def write_json(
    store: ObjectStore, file_name: str, data: dict[str, Any]
) -> None:
    if type(store) is S3Store:
        # This should work for all store types according to obstore docs, but in reality only S3Store seems to support it.
        store.put(
            file_name,
            json.dumps(data, indent=2).encode("utf-8"),
            attributes={"Content-Type": "application/json"},
        )
    elif type(store) is LocalStore:
        # This should be supported https://developmentseed.org/obstore/latest/api/store/local/#obstore.store.LocalStore.put
        full_path = Path(os.path.abspath(store.prefix)) / file_name
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    elif type(store) is HTTPStore:
        # This should be supported https://developmentseed.org/obstore/latest/api/store/http/#obstore.store.HTTPStore.put
        raise NotImplementedError("HTTPStore does not support writing files (even though it is in the obstore docs).")


def get_store_with_prefix_from_url(
    url: str, mkdir: bool = True, **kwargs: dict
) -> ObjectStore:
    url = url.rstrip("/").lower() # Handle uppercase letters or trailing slash
    if url.startswith("s3://"):
        return from_url(url, credential_provider=Boto3CredentialProvider(), **kwargs) # S3 doesn't support mkdir
    elif url.startswith("http://") or url.startswith("https://"):
        return from_url(url, **kwargs)
    elif url.startswith("file://"):
        return from_url(url, mkdir=mkdir, **kwargs) # Can't have a credential provider for local
    else:
        # File URLs that don't start with "file://" need it prepended for from_url and to be made absolute
        abs_url = os.path.abspath(url)
        return from_url(f"file://{abs_url}", mkdir=mkdir, **kwargs)


def get_url_from_store(store: ObjectStore) -> str:
    if type(store) is HTTPStore:
        return store.url
    elif type(store) is S3Store:
        return f"s3://{store.config['bucket']}/{store.prefix}"
    elif type(store) is LocalStore:
        return store.prefix
    else:
        raise ValueError(f"Unsupported store type: {type(store)}")


def split_path_and_file_name_from_url(url: str) -> tuple[str, str]:
    # Get last "/" and return everything after it as the file name
    file_name = urlparse(url).path.split("/")[-1]
    path = url.replace(file_name, "").rstrip("/")
    return path, file_name


def read_dict(store: ObjectStore, file_name: str) -> dict[str, Any]:
    with BytesIO(store.get(file_name).bytes()) as buffer:
        try:
            json_dict = json.load(buffer)
            return json_dict
        except Exception as e:
            logging.error(f"Failed to read dict from {file_name} with exception {e}", exc_info=True)
            raise


def read_geospatial_file(url: str, **kwargs: dict) -> gpd.GeoDataFrame:
    path, file_name = split_path_and_file_name_from_url(url)
    store = get_store_with_prefix_from_url(path)

    with BytesIO(store.get(file_name).bytes()) as buffer:
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
    store: ObjectStore
) -> list[dict[str, Any]]:
    list_of_stac_files = []

    logging.info("Listing STAC items in store recursively")

    # TODO: There is a function find_matching_files in cli_dataset_aca.py that could be moved here and used instead of this part of get_stac_item_dicts_from_store.
    for i, batch in enumerate(store.list(chunk_size=1000)): # default chunk_size is 50 which is very low just to list files
        logging.info(f"Batch number {i + 1} of {len(batch)} files...")
        for stac_file in batch:
            if stac_file["path"].endswith(".stac-item.json"):
                list_of_stac_files.append(stac_file)

    logging.info(f"Found {len(list_of_stac_files)} STAC items.")

    # Use semaphore to limit concurrent requests to prevent S3 from timing out. Otherwise it would request all concurrently and sometimes time out.
    semaphore = asyncio.Semaphore(1000)
    async def _fetch_item(store: ObjectStore, stac_file: dict) -> dict:
        async with semaphore:
            obj = await store.get_async(stac_file["path"])
            data = BytesIO(obj.bytes())
            return json.load(data)
    return await asyncio.gather(*(_fetch_item(store, stac_file) for stac_file in list_of_stac_files))


def write_gdf_to_parquet(
    gdf: gpd.GeoDataFrame, store: ObjectStore, file_name: str
) -> None:
    # Write GeoDataFrame to a GeoParquet file in memory
    with BytesIO() as parquet_buffer:
        gdf.to_parquet(parquet_buffer, engine="pyarrow")
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        store.put(file_name, parquet_buffer.getvalue())
