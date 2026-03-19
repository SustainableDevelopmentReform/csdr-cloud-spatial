import asyncio
import logging
from io import BytesIO

import typer
from requests import get

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
)

cwa_app = typer.Typer()
logger = logging.getLogger(__name__)

geometry_name = "GA Coastal Waters Areas"


# TODO: Make this a reusable function in csdr.io or csdr.utils so it can be used in other geometry caching CLIs
# E.g. utils.cache_zipped_shapefile(source_url, target_location, target_file_name, overwrite)
async def run_cache_cwa(
    source_url: str,
    target_location: str,
    overwrite: bool,
) -> str:
    # Downloads the cwa zip of shapefile from source_url and stores it at target_location
    # Source url is http://
    # Target location can be s3:// or local file path
    target_location = target_location.rstrip("/")
    logger.info(
        f"Caching '{geometry_name}' from '{source_url}' to '{target_location}'..."
    )
    target_path = target_location  # This is the path, there is no file name
    target_file_name = "cwa.zip"
    target_store = get_store_with_prefix_from_url(target_path)

    if exists(target_store, target_file_name) and not overwrite:
        logger.info(
            "File already exists at target location and overwrite is off, skipping download."
        )
        raise typer.Exit(code=0)  # Exit successfully, nothing to do

    logger.info("File doesn't exist or overwrite is on. Re-downloading.")

    logger.info(
        f"Downloading {target_file_name} from {source_url} to {target_location}..."
    )

    # Dowload zip

    response = get(source_url)
    response.raise_for_status()  # Raise an error if the download failed
    zip_bytes = BytesIO(response.content)
    await target_store.put_async(target_file_name, zip_bytes)

    return target_location


# Download zipped shapefile.
@cwa_app.command("cache")
def cache_cwa(
    source_url: str = typer.Option(
        help=f"URL of the source {geometry_name} zipped shapefile to cache.",
        # TODO: Change CRS?
        default="https://hub.arcgis.com/api/v3/datasets/37a401e932544c88828a7d099880afb5_1/downloads/data?format=shp&spatialRefId=4283&where=1%3D1",
    ),
    target_location: str = typer.Option(
        help=f"Local or remote path (like './cache/geometries/cwa/0-0-1/raw' or s3://csdr-public-dev/geometries/cwa/0-0-1/raw) to store the cached {geometry_name} file.",
        default="./cache/geometries/cwa/0-0-1/raw",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing zip file if it exists."
    ),
) -> None:
    logger.info(f"Starting '{geometry_name}' caching process...")

    result_path = asyncio.run(run_cache_cwa(source_url, target_location, overwrite))
    logger.info(
        f"'{geometry_name}' caching process completed. Cached to '{result_path}'"
    )
