# Australian Coastal Sediment Compartments - Secondary Compartments
# UI to download https://digital.atlas.gov.au/datasets/digitalatlas::australian-coastal-sediment-compartments-secondary-compartments/explore
# URL to download shapefile https://hub.arcgis.com/api/v3/datasets/2af87180973d44b0b5b73583e3c06957_2/downloads/data?format=shp&spatialRefId=4283&where=1%3D1
import asyncio
import logging
from io import BytesIO

import typer
from requests import get

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
)

acsc2_app = typer.Typer()


geometry_name = "Australian Coastal Sediment Compartments - Secondary Compartments"


async def run_cache_acsc2(
    source_url: str,
    target_location: str,
    overwrite: bool,
) -> str:
    # Downloads the acsc2 zip of shapefile from source_url and stores it at target_location
    # Source url is http://
    # Target location can be s3:// or local file path
    target_location = target_location.rstrip("/")
    logging.info(f"Caching '{geometry_name}' from '{source_url}' to '{target_location}'...")
    target_path = target_location # This is the path, there is no file name
    target_file_name = "acsc2.zip"
    target_store = get_store_with_prefix_from_url(target_path)

    if exists(target_store, target_file_name) and not overwrite:
        logging.info("File already exists at target location and overwrite is off, skipping download.")
        raise typer.Exit(code=0)  # Exit successfully, nothing to do

    logging.info("File doesn't exist or overwrite is on. Re-downloading.")

    logging.info(f"Downloading {target_file_name} from {source_url} to {target_location}...")

    # Dowload zip
    
    response = get(source_url)
    response.raise_for_status()  # Raise an error if the download failed
    zip_bytes = BytesIO(response.content)
    await target_store.put_async(target_file_name, zip_bytes)

    return target_location


# Download zipped shapefile.
@acsc2_app.command("cache")
def cache_acsc2(
    source_url: str = typer.Option(
        help=f"URL of the source {geometry_name} zipped shapefile to cache.",
        # TODO: Change CRS?
        default="https://hub.arcgis.com/api/v3/datasets/2af87180973d44b0b5b73583e3c06957_2/downloads/data?format=shp&spatialRefId=4283&where=1%3D1",
    ),
    target_location: str = typer.Option(
        help=f"Local or remote path (like './cache/geometries/acsc2/0-0-1/raw' or s3://csdr-public-dev/geometries/acsc2/0-0-1/raw) to store the cached {geometry_name} file.",
        default="./cache/geometries/acsc2/0-0-1/raw",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing zip file if it exists."
    ),
) -> None:
    logging.info(f"Starting '{geometry_name}' caching process...")

    result_path = asyncio.run(run_cache_acsc2(source_url, target_location, overwrite))
    logging.info(f"'{geometry_name}' caching process completed. Cached to '{result_path}'")
