import asyncio
import logging

import requests
import typer
from obstore.store import ObjectStore

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    split_path_and_file_name_from_url,
)

aus_states_app = typer.Typer()

# Steps:
# 1. Cache: Download the ABS states shapefile zip from ABS website to target_location
# 2. Extract: Unzip the shapefile and write to target_location
# 3. Index: Read the shapefile and write to a single geoparquet file

async def _run_cache_aus_states(source_url: str, source_file_name: str, target_store: ObjectStore) -> None:
    # Download the file from source_url to target_location
    response = requests.get(source_url)
    response.raise_for_status()  # Raise an error for bad status codes
    # Save the zipfile to the target store
    await target_store.put_async(source_file_name, response.content)

@aus_states_app.command("cache")
def cache_aus_states(
    source_url: str = typer.Option(
        "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files/STE_2021_AUST_SHP_GDA2020.zip",
        help="URL containing zipped shapefile of ABS Australian States and Territories (e.g. https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files/STE_2021_AUST_SHP_GDA2020.zip)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write unzipped files (e.g. s3://bucket/datasets/aus-states/0-0-1/raw)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite files if they exist at target."),
) -> None:
    logging.info("Starting Australian States caching process ...")
    target_location = target_location.rstrip("/")

    target_store = get_store_with_prefix_from_url(target_location)
    _source_path, source_file_name = split_path_and_file_name_from_url(source_url)
    target_url = f"{target_location}/{source_file_name}"
    logging.info(f"Target URL for caching is {target_url}")

    if exists(target_store, source_file_name) and not overwrite:
        logging.info("File already exists at target location and overwrite is disabled. Skipping download.")
        exit(0) # Exit successfully, nothing to do
    logging.info("File either doesn't exist at target location or overwrite is enabled. Re-downloading.")

    asyncio.run(_run_cache_aus_states(source_url, source_file_name, target_store))

    logging.info(f"Australian States caching process completed. Downloaded to {target_url}")
