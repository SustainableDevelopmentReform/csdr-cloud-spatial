# Set Rust logging environment variables BEFORE importing rustac
import asyncio
import json
import sys
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import urlparse
from zipfile import ZipFile

import typer
import xarray as xr
from loguru import logger
from obstore.store import HTTPStore, LocalStore, S3Store
from odc.geo.cog import write_cog
from rio_stac import create_stac_item
from rioxarray import open_rasterio
from rustac import write

from csdr.io import (
    exists,
    get_prefix,
    get_stac_item_dicts_from_store,
    get_store_for_url,
    get_url_from_store_filename,
)
from csdr.utils import suppress_rust_output

aca_app = typer.Typer()



async def unzip_single_region(
    source_url: str,
    target_location: str,
    target_path: str,
    target_zip_name: str,
    overwrite: bool,
    semaphore: asyncio.Semaphore,
) -> str:
    # Unzip a single region file in S3
    async with semaphore:
        logger.info(f"Unzipping ACA region from {source_url} to {target_location}...")

        url = urlparse(source_url)

        source = HTTPStore(f"{url.scheme}://{url.netloc}")

        # Must check this file exists before proceeding
        source_exists = exists(source, url.path)
        if not source_exists:
            logger.error(f"Source file does not exist at {source_url}. Cannot extract.")
            return
        else:
            logger.info(
                f"Source file found at {source_url}, proceeding with extraction."
            )

        source_meta = source.head(url.path)
        size = source_meta.get("size", None)

        dest = None
        dest = get_store_for_url(target_location)
        target_zip_name = (
            f"{target_path}/{target_zip_name}"
            if target_path is not None
            else target_zip_name
        )



async def run_extract_aca(
    source_location: str,
    source_zip_name: str,
    target_location: str,
    overwrite: bool,
    max_concurrent: int,
) -> None:
    # Async function to run the ACA extraction with parallel processing.
    store = None
    s3_prefix = None

    # Cleanup...
    source_location = source_location.rstrip("/")

    store = get_store_for_url(source_location)

    if type(store) is S3Store:
        s3_prefix = get_prefix(source_location)
        if s3_prefix is not None:
            source_zip_name = f"{s3_prefix}/{source_zip_name}"

    logger.info(f"Checking for source zip file at path {source_zip_name}...")
    if type(store) is S3Store:
        logger.info(
            f"Store is S3Store with bucket {store.config['bucket']} and prefix {s3_prefix}"
        )

    source_exists = exists(store, source_zip_name)
    if not source_exists:
        logger.error(
            f"Source zip file does not exist at {source_location}. Cannot extract."
        )
        raise typer.Exit(code=1)
    else:
        logger.info(
            f"Source zip file found at {source_location}, proceeding with extraction."
        )

    target_store = get_store_for_url(target_location)

    # Open the zip file, and extract all files into memory
    # Load the file as bytes first
    logger.info("Loading data into memory")
    zip_bytes = BytesIO(store.get(source_zip_name).bytes())
    logger.info("Finished loading data")

    # Create semaphore to limit concurrent operations
    semaphore = asyncio.Semaphore(max_concurrent)

    with ZipFile(zip_bytes) as z:
        # Get list of TIF files to process
        tif_files = [name for name in z.namelist() if name.endswith(".tif")]
        logger.info(
            f"Found {len(tif_files)} TIF files to process with max {max_concurrent} concurrent operations."
        )

        # Create tasks for parallel processing
        tasks = [
            process_single_file(
                name, z, target_location, target_store, overwrite, semaphore
            )
            for name in tif_files
        ]

        # Execute all tasks concurrently
        await asyncio.gather(*tasks)

    logger.info("ACA extraction process completed.")


@aca_app.command("extract")
def extract_aca(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the extracted ACA files.",
        default="./cache/aca",
    ),
    source_zip_name: str = typer.Option(
        help="Name of the zip file to extract the ACA data from.",
        default="s3://csdr-public-dev/datasets/aca/0-0-1/cache/*.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the extracted ACA files.",
        default="./cache/aca",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing files during extraction."
    ),
    max_concurrent: int = typer.Option(
        32, help="Maximum number of files to process concurrently."
    ),
) -> None:
    logger.info("Starting ACA extraction process...")
    asyncio.run(
        run_extract_aca(
            source_location, source_zip_name, target_location, overwrite, max_concurrent
        )
    )


async def run_index_aca(
    source_location: str, target_location: str, overwrite: bool = True
) -> None:
    store = get_store_for_url(source_location)
    s3_prefix = None
    if type(store) is S3Store:
        s3_prefix = get_prefix(source_location)

    dest = get_store_for_url(target_location)

    out_filename = "aca.parquet"
    dest_s3_prefix = None

    if type(dest) is S3Store:
        dest_s3_prefix = get_prefix(target_location)
        if dest_s3_prefix is not None:
            out_filename = f"{dest_s3_prefix}/{out_filename}"

    # Check for existing geoparquet file
    if exists(dest, out_filename) and not overwrite:
        logger.info(
            f"Parquet file already exists at {out_filename}, skipping indexing."
        )
        return
    else:
        if overwrite:
            logger.info("Overwrite is enabled, re-indexing ACA.")
        else:
            logger.info("Parquet file does not exist, proceeding with indexing.")

    # Find all the the ACA STAC files
    item_dicts = await get_stac_item_dicts_from_store(store, s3_prefix)

    result_location = get_url_from_store_filename(dest, out_filename)

    logger.info(f"Writing {len(item_dicts)} STAC items to parquet at {result_location}")
    with suppress_rust_output():
        await write(out_filename, item_dicts, store=dest)

    logger.info(f"Parquet write completed, wrote to {result_location}")


@aca_app.command("index")
def index_aca(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to the ACA files.",
        default="./cache/aca",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the indexed ACA parquet file.",
        default="./cache/aca",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logger.info("Starting ACA indexing process...")
    asyncio.run(run_index_aca(source_location, target_location, overwrite))
    logger.info("ACA indexing process completed.")


@aca_app.command("unzip")
def unzip_aca(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to the ACA files.",
        default="./cache/aca",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the merged ACA parquet file.",
        default="./cache/aca",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logger.info("Starting ACA unzipping process...")
    asyncio.run(run_unzip_aca(source_location, target_location, overwrite))
    logger.info("ACA unzipping process completed.")

async def run_unzip_aca(
    source_location: str, target_location: str, overwrite: bool = True
) -> None:
    store = get_store_for_url(source_location)
    s3_prefix = None
    if type(store) is S3Store:
        s3_prefix = get_prefix(source_location)

    dest = get_store_for_url(target_location)

    dest_s3_prefix = None

    if type(dest) is S3Store:
        dest_s3_prefix = get_prefix(target_location)
        if dest_s3_prefix is not None:
            out_filename = f"{dest_s3_prefix}/{out_filename}"

    """
    # Check for existing unzipped reef extent gpkg files
    if exists(dest, out_filename) and not overwrite: # update this to check for unzipped reef extent gpkg files instead of parquet
        logger.info(
            f"unzipped reef extent gpkg files already exist at {out_filename}, skipping indexing."
        )
        return
    else:
        if overwrite:
            logger.info("Overwrite is enabled, re-unzipping ACA.")
        else:
            logger.info("Unzipped reef extent gpkg files do not exist, proceeding with unzipping.")
    """
    # do the unzipping here
    logger.info("Unzipping")


@aca_app.command("merge")
def merge_aca(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to the ACA files.",
        default="./cache/aca",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the merged ACA parquet file.",
        default="./cache/aca",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logger.info("Starting ACA merging process...")
    asyncio.run(run_merge_aca(source_location, target_location, overwrite))
    logger.info("ACA merging process completed.")

# need to do:
#     extract: unzip all region zip files
#     merge: merge all reef extent gpkg into one geoparquet
#     clean: clean up the unzipped files? keep the zips
