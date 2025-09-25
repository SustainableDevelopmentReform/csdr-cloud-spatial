# Set Rust logging environment variables BEFORE importing rustac
import asyncio

import typer
from loguru import logger
from obstore.store import S3Store
from rasterio import Env
from rustac import write

from csdr.io import (
    exists,
    get_prefix,
    get_stac_item_dicts_from_store,
    get_store_for_url,
)
from csdr.utils import suppress_rust_output

seagrass_app = typer.Typer()


async def run_index_dep_seagrass(
    source_location: str, target_location: str, overwrite: bool = True
) -> None:
    store = get_store_for_url(source_location, region="us-west-2")
    s3_prefix = get_prefix(source_location)

    dest = get_store_for_url(target_location)
    out_filename = "dep_s2_seagrass.parquet"

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
            logger.info("Overwrite is enabled, re-indexing.")
        else:
            logger.info("Parquet file does not exist, proceeding with indexing.")

    # Find all the the DEP Seagrass STAC files
    with Env(AWS_REGION="us-west-2"):
        item_dicts = await get_stac_item_dicts_from_store(store, s3_prefix)

    logger.info(
        f"Writing {len(item_dicts)} STAC items to parquet at {target_location}/{out_filename}"
    )
    with suppress_rust_output():
        await write(out_filename, item_dicts, store=dest)

    logger.info("Parquet write completed.")

    if target_location.startswith("s3://"):
        logger.info(f"Finished writing to s3://{dest.config['bucket']}/{out_filename}")
    else:
        logger.info(f"Finished writing to {target_location}/{out_filename}")


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
