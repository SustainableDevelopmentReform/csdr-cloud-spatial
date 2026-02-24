# Set Rust logging environment variables BEFORE importing rustac
import asyncio
import logging

import typer
from rustac import search_to

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
)
from csdr.utils import suppress_rust_output

seagrass_app = typer.Typer()


async def run_index_dep_seagrass(
    stac_api_url: str, target_location: str, overwrite: bool = True
) -> None:
    target_filename = "dep_s2_seagrass.parquet"
    target_store = get_store_with_prefix_from_url(target_location, mkdir=True)
    target_url = f"{target_location}/{target_filename}"
    logging.info(f"Target URL for DEP Seagrass parquet: {target_url}")

    # Check for existing geoparquet file
    if exists(target_store, target_filename) and not overwrite:
        logging.info(
            f"Parquet file already exists at {target_filename}, skipping indexing."
        )
        return
    else:
        if overwrite:
            logging.info("Overwrite is enabled, re-indexing.")
        else:
            logging.info("Parquet file does not exist, proceeding with indexing.")

    with suppress_rust_output():
        # TODO: experiment with parquet_compression options for rustac write
        count_items = await search_to(
            target_filename,
            stac_api_url,
            collections=["dep_s2_seagrass"],
            store=target_store,
        )

    logging.info(f"Written {count_items} STAC items to parquet at {target_url}")
    if count_items == 0:
        logging.error("No STAC items found, nothing to index.")
        exit(1)  # Exit with error code

    logging.info(f"Finished writing parquet file to {target_url}")


# Read all STAC items from DEP Seagrass bucket path and index them into a single STAC-Geoparquet file using rustac.
@seagrass_app.command("index")
def index_dep_seagrass(
    stac_api_url: str = typer.Option(
        ...,
        help="URL to the STAC API e.g. https://stac.prod.digitalearthpacific.io",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to store the indexed DEP Seagrass parquet file.",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logging.info("Starting DEP Seagrass indexing process...")
    asyncio.run(run_index_dep_seagrass(stac_api_url, target_location, overwrite))
    logging.info("DEP Seagrass indexing process completed.")
