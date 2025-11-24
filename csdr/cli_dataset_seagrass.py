# Set Rust logging environment variables BEFORE importing rustac
import asyncio

import typer
from loguru import logger
from rasterio import Env
from rustac import write

from csdr.io import (
    exists,
    get_prefix_from_url,
    get_stac_item_dicts_from_store,
    get_store_from_url,
)
from csdr.utils import suppress_rust_output

seagrass_app = typer.Typer()


async def run_index_dep_seagrass(
    source_location: str, target_location: str, overwrite: bool = True
) -> None:
    store = get_store_from_url(source_location, region="us-west-2")
    source_prefix = get_prefix_from_url(source_location)

    target_store = get_store_from_url(target_location)
    target_filename = "dep_s2_seagrass.parquet"
    target_url = f"{target_location}/{target_filename}"
    logger.info(f"Target URL for DEP Seagrass parquet: {target_url}")
    
    # Check for existing geoparquet file
    if exists(target_store, target_filename) and not overwrite:
        logger.info(
            f"Parquet file already exists at {target_filename}, skipping indexing."
        )
        return
    else:
        if overwrite:
            logger.info("Overwrite is enabled, re-indexing.")
        else:
            logger.info("Parquet file does not exist, proceeding with indexing.")

    # Find all the the DEP Seagrass STAC files
    with Env(AWS_REGION="us-west-2"):
        item_dicts = await get_stac_item_dicts_from_store(store, source_prefix)

    logger.info(
        f"Writing {len(item_dicts)} STAC items to parquet at {target_url}"
    )
    with suppress_rust_output():
        await write(target_filename, item_dicts, store=target_store)

    logger.info(f"Finished writing parquet file to {target_url}")


@seagrass_app.command("index-dep")
def index_dep_seagrass(
    source_location: str = typer.Option(
        help="S3 path to the bucket with Seagrass STAC documents.",
        default="s3://dep-public-data/dep_s2_seagrass/0-2-0",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the indexed DEP Seagrass parquet file.",
        default="./cache/seagrass",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logger.info("Starting DEP Seagrass indexing process...")
    asyncio.run(run_index_dep_seagrass(source_location, target_location, overwrite))
    logger.info("DEP Seagrass indexing process completed.")
