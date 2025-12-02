import asyncio
import logging
import re
from io import BytesIO

import pandas as pd
import typer
from obstore.store import ObjectStore

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
)

buildings_app = typer.Typer()


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
                list_of_matching_files.append(item["path"]) # Append a the path string.

    logging.info(f"Found {len(list_of_matching_files)} matching items.")

    return list_of_matching_files


async def _download_parquet_file(source_store: ObjectStore, country_iso: str, target_store: ObjectStore, overwrite: bool, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        # Get file and write to target_store
        file_name = f"country_iso={country_iso}/{country_iso}.parquet"
        if exists(target_store, file_name):
            if not overwrite:
                logging.info(f"Skipping {file_name}; output exists at target store and overwrite is off.")
                return
            else:
                logging.info(f"Overwrite is on. Re-downloading {file_name} ...")
        source_store.get(file_name)
        logging.info(f"Downloading {file_name} to target store ...")
        await target_store.put_async(file_name, source_store.get(file_name)) # Do this async so it doesn't block other downloads.


async def _run_extract_buildings(
    source_location: str,
    target_location: str,
    overwrite: bool,
    max_concurrent: int,
) -> None:
    logging.info("Scraping source coop for all parquet files...")
    source_url = "https://source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country/"
    logging.info(f"Root: {source_url}...")
    source_store = get_store_with_prefix_from_url(source_location)
    target_store = get_store_with_prefix_from_url(target_location)

    countries_to_extract = []
    countries_to_extract = ["AFG", "AGO", "ALB", "AND", "ARE", "ARG", "ARM", "ATG", "AUS"] # TODO: Get all dynamically from source_store listing
    logging.info(f"Found {len(countries_to_extract)} country files to extract from {source_location}. Extracting...")

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _download_parquet_file(
            source_store=source_store,
            country_iso=country_iso,
            target_store=target_store,
            overwrite=overwrite,
            semaphore=semaphore,
        )
        for country_iso in countries_to_extract
    ]
    await asyncio.gather(*tasks)
    logging.info("Completed extracting all region zips.")


# Buildings Extract gets all zip files from source_location, unzips them to target_location, preserving folder structure.
# We need to get all 185 partitioned parquet files (per country or country/sa2). Not sure if we should do this manually or as part of extract.
# Then extract doesn't need to unzip.
# # https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country/country_iso=AFG/AFG.parquet
@buildings_app.command("extract")
def extract_buildings(
    source_location: str = typer.Option(
        "https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country/",
        help="HTTP url containing parquet files to extract (e.g. https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country/)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write unzipped files (e.g. s3://bucket/datasets/buildings/0-0-1/data)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite files if they exist at target."),
    max_concurrent: int = typer.Option(16, help="Maximum number of unzips to process at once."),
) -> None:
    logging.info("Starting buildings extraction process ...")
    source_location = source_location.rstrip("/")
    target_location = target_location.rstrip("/")
    asyncio.run(_run_extract_buildings(source_location, target_location, overwrite, max_concurrent))
    logging.info("Buildings extraction process completed.")

async def _run_index_buildings(
    source_location: str,
    target_location: str,
    overwrite: bool,
) -> None:
    target_store = get_store_with_prefix_from_url(target_location)
    target_file_name = "buildings.parquet"
    if exists(target_store, target_file_name) and not overwrite:
        logging.info(f"Skipping index: {target_file_name} already exists and overwrite is off.")
        return
    source_store = get_store_with_prefix_from_url(source_location)
    logging.info(f"Searching for all parquet files under {source_location} ...")
    parquet_paths = _find_matching_files(source_store, r"\.parquet$")
    logging.info(f"Found {len(parquet_paths)} parquet files to merge into {target_file_name}")
    dfs = []
    for path in parquet_paths:
        logging.info(f"Reading {path} ...")
        # Get a file-like object from obstore, read it into a GeoDataFrame, append gdf to list.
        bytes_obj = source_store.get(path).bytes()
        df = pd.read_parquet(BytesIO(bytes_obj))
        dfs.append(df)
    if not dfs:
        logging.warning("No Parquet files found, nothing to merge.")
        return
    merged_df = pd.concat(dfs, ignore_index=True)
    logging.info(f"Writing merged Parquet to {target_file_name}")
    with BytesIO() as f:
        merged_df.to_parquet(f, index=False)
        f.seek(0)
        target_store.put(target_file_name, f.read())
    logging.info("Merge and export to Parquet completed.")


# Buildings Index gets all of the parquet files (one per country), and merges them into a single buildings.parquet file at target_location.
@buildings_app.command("index")
def index_buildings(
    source_location: str = typer.Option(
        ...,
        help="S3 or local path to extracted buildings files (e.g. s3://bucket/datasets/buildings/0-0-1/data)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write buildings.parquet index (e.g. s3://bucket/datasets/buildings/0-0-1/)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite output file if it exists."),
) -> None:
    logging.info("Starting buildings merge/index process ...")
    asyncio.run(_run_index_buildings(source_location, target_location, overwrite))
    logging.info("buildings merge/index process completed.")

if __name__ == "__main__":
    buildings_app()
