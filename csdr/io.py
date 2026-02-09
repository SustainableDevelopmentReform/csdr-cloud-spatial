from stac_geoparquet.arrow import parse_stac_items_to_arrow, to_parquet
import asyncio
import json
import logging
import os
import re
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import geoarrow.pyarrow as ga
import geopandas as gpd
import pandas as pd
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import HTTPStore, LocalStore, ObjectStore, S3Store, from_url
# from pyarrow import ArrowInvalid, Table


class CSDRException(Exception):
    pass

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
        raise CSDRException("HTTPStore does not support writing files (even though it is in the obstore docs).")


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
        raise CSDRException(f"Unsupported store type: {type(store)}")


def split_path_and_file_name_from_url(url: str) -> tuple[str, str]:
    # Get last "/" and return everything after it as the file name
    file_name = url.rsplit("/", 1)[-1]
    path = url[:-(len(file_name))].rstrip("/")
    return path, file_name


def read_dict(store: ObjectStore, file_name: str) -> dict[str, Any]:
    with BytesIO(store.get(file_name).bytes()) as buffer:
        try:
            json_dict = json.load(buffer)
            return json_dict
        except Exception:
            logging.exception(f"Failed to read dict from {file_name}.")
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
        # except ArrowInvalid:
        #     # Try loading as generic file
        #     buffer.seek(0)
        #     try:
        #         gdf = gpd.read_file(buffer, **kwargs)
        #         return gdf
        #     except Exception:
        #         logging.exception(
        #             f"Failed to read geospatial file from {url}."
        #         )
        #         raise


def find_matching_files(store: ObjectStore, pattern: str, prefix: str | None = None) -> list[str]:
    """
    Finds files in the store with a given glob pattern (recursively).
    """
    list_of_matching_files = []
    logging.info("Listing items in store recursively")
    regex = re.compile(pattern)
    for i, batch in enumerate(store.list(prefix=prefix, chunk_size=1000)):
        logging.info(f"Batch number {i + 1} of {len(batch)} files...")
        for item in batch:
            if regex.search(item["path"]):
                list_of_matching_files.append(item["path"]) # Append the path string.

    logging.info(f"Found {len(list_of_matching_files)} matching items.")

    return list_of_matching_files


async def get_stac_item_dicts_from_store(
    store: ObjectStore
) -> list[dict[str, Any]]:
    list_of_stac_files = []

    logging.info("Listing STAC items in store recursively")

    # TODO: There is a function io.find_matching_files that could be used instead of this part of get_stac_item_dicts_from_store.
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
        # Use geoarrow for geometry column for vizualisation in the CSDR app.
        # Write covering bbox for better querying capabilities.
        # TODO: Experiment with parquet_compression options. Defaults to snappy.
        gdf.to_parquet(parquet_buffer, index=False, engine="pyarrow", geometry_encoding="geoarrow", write_covering_bbox=True) 
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        store.put(file_name, parquet_buffer.getvalue())


# def stac_items_to_arrow(item_dicts: list[dict[str, Any]]) -> Table:
def stac_items_to_arrow(item_dicts: list[dict[str, Any]]) -> any:
    # Use Arrow. This is needed for vizualisation in the CSDR app.
    # Could alternatively use rustac.to_arrow instead of parse_stac_items_to_arrow. I don't think either properly processes the geometry column to arrow native type.
    # arrow_table = rustac.to_arrow(item_dicts)

    # Could use this lightweight lib instead of pyarrow: https://kylebarron.dev/arro3/latest/api/io/parquet/#arro3.io.write_parquet

    # This (parse_stac_items_to_arrow) may not be the best approach, but it works for now.
    record_batch_reader = parse_stac_items_to_arrow(item_dicts)
    table = record_batch_reader.read_all()

    # to_parquet(
    #     table,
    #     output_path=target_url,
    # )

    # Convert WKB to native encoding. This is needed because otherwise the geometry column has type binary not geoarrow.
    geom_col = table.column('geometry')
    native_geom = ga.as_geoarrow(geom_col, coord_type=ga.CoordType.SEPARATED)
    # Replace column
    new_table = table.set_column(
        table.schema.get_field_index('geometry'),
        'geometry',
        native_geom
    )
    print(new_table.schema)

    return new_table


def write_arrow_to_parquet(
    table: any, store: ObjectStore, file_name: str
    # table: Table, store: ObjectStore, file_name: str
) -> None:
    # # TODO: Is this compliant STAC-Geoparquet? I need a bbox metadata field, which is part of the standard. Maybe I regressed by moving away from rustac.write.
    # # Prepare metadata dict if bbox is provided
    # metadata = None
    # # TODO: Use someone else's better code to write compliant STAC-Geoparquet files instead of this. This is a bit hacky and is not fully compliant.
    # bbox = get_bbox_from_arrow_table(table)
    # if bbox is not None:
    #     # Parquet expects bytes for keys/values
    #     metadata = {b"bbox": json.dumps(bbox).encode("utf-8")}

    # # Write Arrow Table to Parquet in memory, then put to store
    # with BytesIO() as buf:
    #     Can't use pq.write because it doesn't write compliant STAC-Geoparquet.
    #     pq.write_table(table, buf, metadata=metadata)
    #     buf.seek(0)
    #     store.put(file_name, buf.getvalue())
        # Write to memory buffer first
    # with BytesIO() as buf:
    #     # to_parquet handles everything:
    #     # - Converts items to Arrow Table
    #     # - Adds GeoParquet metadata
    #     # - Creates bbox column from geometries
    #     # - Uses proper Arrow geometry encoding
    #     to_parquet(
    #         table,
    #         buf,
    #         # Optional: add schema if you want to customize
    #         # schema=your_custom_schema
    #     )
    #     buf.seek(0)
    #     store.put(file_name, buf.getvalue())

    """
    Write Arrow Table to STAC-GeoParquet format compliant with the spec.
    Uses stac-geoparquet library for full compliance.
    """
    # Use a temporary file since to_parquet requires a file path
    # This is the cleanest approach for full spec compliance
    # Workaround because stac_geoparquet.arrow.to_parquet doesn't support writing to a store.
    # Could look at obstore.fsspec.FsspecStore.
    # Writing to a temp file and then doing obstore.put is not efficient, but it works.
    with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as tmp:
        tmp_path = Path(tmp.name)
    
    # try:
    # Write STAC-GeoParquet with proper metadata
    # Does the path need the filename too?
    # I am hitting this issue https://github.com/apache/arrow/issues/44696
    to_parquet(table, tmp_path) # It has to be this function. Just need to get around the error "ArrowKeyError: Attempted to register factory for scheme 'file' but that scheme is already registered."
    # Might have to migrate to conda to try resolve this.

    #     # Upload to object store
    #     with open(tmp_path, 'rb') as f:
    #         store.put(file_name, f.read())
    # finally:
    #     # Clean up
    #     tmp_path.unlink(missing_ok=True)
