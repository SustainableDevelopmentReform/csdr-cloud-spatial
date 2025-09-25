import asyncio
import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import geopandas as gpd
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import HTTPStore, LocalStore, S3Store


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
        # No put, so do it a boring way
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


def get_prefix(url: str) -> str | None:
    s3_url = urlparse(url)
    file_name = get_file_name_from_url(url)

    s3_prefix = None
    if file_name is not None and s3_url.path.endswith(file_name):
        s3_prefix = s3_url.path.lstrip("/").replace(file_name, "").rstrip("/")
    else:
        s3_prefix = s3_url.path.lstrip("/").rstrip("/")

    if s3_prefix == "":
        s3_prefix = None

    if s3_prefix is not None:
        s3_prefix = s3_prefix.lstrip("/").rstrip("/")

    return s3_prefix


def get_dataset_name_from_url(
    store: HTTPStore | S3Store | LocalStore, url: str, keep_path: bool = True
) -> str:
    dataset_name = get_file_name_from_url(url)
    if dataset_name is None:
        raise ValueError(f"Could not determine dataset name from URL: {url}")

    if keep_path:
        if type(store) in (S3Store, HTTPStore):
            s3_prefix = get_prefix(url)
            if s3_prefix is not None:
                dataset_name = f"{s3_prefix}/{dataset_name}"

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


def read_geospatial_file(url: str, **kwargs: dict) -> gpd.GeoDataFrame:
    store = get_store_for_url(url)
    path = get_dataset_name_from_url(store, url)

    with BytesIO(store.get(path).bytes()) as buffer:
        # TODO: Make it read more things, not just parquet
        gdf = gpd.read_parquet(buffer, **kwargs)
        return gdf


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
