import asyncio
import logging
import os
import re
from io import BytesIO
from zipfile import ZipFile

import geopandas as gpd
import pandas as pd
import typer
from obstore.store import ObjectStore

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
)

aca_app = typer.Typer()


def _find_matching_files(store: ObjectStore, pattern: str) -> list[str]:
    """
    Finds files in the store with a given glob pattern (recursively).
    """
    list_of_matching_files = []
    logging.info("Listing items in store recursively")
    regex = re.compile(pattern)
    for i, batch in enumerate(store.list(chunk_size=1000)):
        logging.info(f"Batch number {i} of {len(batch)} files...")
        for item in batch:
            if regex.search(item["path"]):
                list_of_matching_files.append(item["path"]) # Append the path string.

    logging.info(f"Found {len(list_of_matching_files)} matching items.")

    return list_of_matching_files


async def _unzip_single_zip(
    zip_path_and_file_name: str,
    source_store: ObjectStore,
    target_store: ObjectStore,
    overwrite: bool,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        source_file_name = os.path.basename(zip_path_and_file_name) # Remove nested path folders (if any). Keep only zip file name and extension
        path_without_extension = os.path.splitext(source_file_name)[0] # Remove .zip extension
        if exists(target_store, f"{path_without_extension}/Reef-Extent/reefextent.gpkg") and not overwrite:
            logging.info(f"Skipping {zip_path_and_file_name}; output exists at target store and overwrite is off.")
            return
        logging.info(f"Unzipping {zip_path_and_file_name} to target store (overwrite={'on' if overwrite else 'off'}) ...")
        zip_bytes = BytesIO(source_store.get(zip_path_and_file_name).bytes())
        with ZipFile(zip_bytes) as zip_item:
            for zip_source_file_name in zip_item.namelist(): # Iterate through each file in the zip
                if zip_source_file_name.endswith("/"): # Skip directories
                    continue
                data = zip_item.read(zip_source_file_name)
                target_path = f"{path_without_extension}/{zip_source_file_name}" # Add the original zip file name (without .zip) as a folder prefix
                # TODO: Is this exists check needed? It is a bit redundant but could be a good safeguard in case parial unzips occured.
                if exists(target_store, target_path) and not overwrite:
                    logging.info(f"Skipping file {target_path}, already exists and overwrite is off.")
                    continue
                target_store.put(target_path, data)
        logging.info(f"Finished unzipping {zip_path_and_file_name} to target store.")


async def _run_extract_aca(
    source_location: str,
    target_location: str,
    overwrite: bool,
    max_concurrent: int,
) -> None:
    logging.info("Finding zip files to extract...")
    # TODO: We could make this much more efficient by somehow just getting the reefextent.gpkg because the whole of Northern Caribbean data is 11.84 GB (unzipped) but the reefextent.gpkg is only 38 MB.
    source_store = get_store_with_prefix_from_url(source_location, client_options={"timeout":"2 hours"}) # timeout is increased because we need to download large files
    target_store = get_store_with_prefix_from_url(target_location)
    all_zip_file_paths = _find_matching_files(source_store, r"\.zip$") # Match all .zip files
    logging.info(f"Found {len(all_zip_file_paths)} zip files to extract from {source_location}. Unzipping...")

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _unzip_single_zip(
            zip_path_and_file_name=zip_file_path,
            source_store=source_store,
            target_store=target_store,
            overwrite=overwrite,
            semaphore=semaphore,
        )
        for zip_file_path in all_zip_file_paths
    ]
    await asyncio.gather(*tasks)
    logging.info("Completed unzipping all region zips.")


# ACA Extract gets all zip files from source_location, unzips them to target_location, preserving folder structure.
@aca_app.command("extract")
def extract_aca(
    source_location: str = typer.Option(
        ...,
        help="S3 or local path containing zip files to extract (e.g. s3://bucket/datasets/aca/0-0-1/raw)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write unzipped files (e.g. s3://bucket/datasets/aca/0-0-1/data)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite files if they exist at target."),
    max_concurrent: int = typer.Option(16, help="Maximum number of unzips to process at once."),
) -> None:
    logging.info("Starting ACA extraction process ...")
    source_location = source_location.rstrip("/")
    target_location = target_location.rstrip("/")
    asyncio.run(_run_extract_aca(source_location, target_location, overwrite, max_concurrent))
    logging.info("ACA extraction process completed.")


async def _run_index_aca(
    source_location: str,
    target_location: str,
    overwrite: bool,
) -> None:
    target_store = get_store_with_prefix_from_url(target_location)
    target_file_name = "reefextent.parquet"
    if exists(target_store, target_file_name) and not overwrite:
        logging.info(f"Skipping index: {target_file_name} already exists and overwrite is off.")
        return
    source_store = get_store_with_prefix_from_url(source_location)
    logging.info(f"Searching for all reefextent.gpkg under {source_location} ...")
    gpkg_paths = _find_matching_files(source_store, "reefextent.gpkg")
    logging.info(f"Found {len(gpkg_paths)} reefextent.gpkg files to merge into {target_file_name}")
    dfs = []
    for path in gpkg_paths:
        logging.info(f"Reading {path} ...")
        # Get a file-like object from obstore, read it into a GeoDataFrame, append gdf to list.
        bytes_obj = source_store.get(path).bytes()
        # TODO: Use io.read_geospatial_file
        gdf = gpd.read_file(BytesIO(bytes_obj))
        dfs.append(gdf)
    if not dfs:
        logging.error("No GPKG files found, nothing to merge.")
        raise ValueError("No GPKG files found to merge.")
    merged_gdf = gpd.GeoDataFrame(pd.concat(dfs, ignore_index=True))
    logging.info(f"Writing merged GeoParquet to {target_file_name}")
    with BytesIO() as f:
        merged_gdf.to_parquet(f, index=False)
        f.seek(0)
        target_store.put(target_file_name, f.read())
    logging.info("Merge and export to GeoParquet completed.")


# ACA Index gets all of the nested reefextent.gpkg files (one per region folder), and merges them into a single reefextent.parquet file at target_location.
@aca_app.command("index")
def index_aca(
    source_location: str = typer.Option(
        ...,
        help="S3 or local path to extracted ACA files (e.g. s3://bucket/datasets/aca/0-0-1/data)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write reefextent.parquet index (e.g. s3://bucket/datasets/aca/0-0-1/)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite output file if it exists."),
) -> None:
    logging.info("Starting ACA merge/index process ...")
    asyncio.run(_run_index_aca(source_location, target_location, overwrite))
    logging.info("ACA merge/index process completed.")

if __name__ == "__main__":
    aca_app()
