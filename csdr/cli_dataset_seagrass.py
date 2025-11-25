# Set Rust logging environment variables BEFORE importing rustac
import asyncio
import logging

import typer
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

    # TODO: make this S3 prefix code a function.
    if type(dest) is S3Store:
        dest_s3_prefix = get_prefix(target_location)
        if dest_s3_prefix is not None:
            out_filename = f"{dest_s3_prefix}/{out_filename}"

    # Check for existing geoparquet file
    if exists(dest, out_filename) and not overwrite:
        logging.info(
            f"Parquet file already exists at {out_filename}, skipping indexing."
        )
        return
    else:
        if overwrite:
            logging.info("Overwrite is enabled, re-indexing.")
        else:
            logging.info("Parquet file does not exist, proceeding with indexing.")

    # Find all the the DEP Seagrass STAC files
    with Env(AWS_REGION="us-west-2"):
        item_dicts = await get_stac_item_dicts_from_store(store, s3_prefix)

    logging.info(
        f"Writing {len(item_dicts)} STAC items to parquet at {target_location}/{out_filename}"
    )
    with suppress_rust_output():
        await write(out_filename, item_dicts, store=dest)

    logging.info("Parquet write completed.")

    if target_location.startswith("s3://"):
        logging.info(f"Finished writing to s3://{dest.config['bucket']}/{out_filename}")
    else:
        logging.info(f"Finished writing to {target_location}/{out_filename}")


# Read all STAC items from DEP Seagrass bucket path and index them into a single STAC-Geoparquet file using rustac.
@seagrass_app.command("index-dep")
def index_dep_seagrass(
    source_location: str = typer.Option(
        ...,
        help="S3 path to the bucket with Seagrass STAC documents.",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to store the indexed DEP Seagrass parquet file.",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logging.info("Starting DEP Seagrass indexing process...")
    asyncio.run(run_index_dep_seagrass(source_location, target_location, overwrite))
    logging.info("DEP Seagrass indexing process completed.")
