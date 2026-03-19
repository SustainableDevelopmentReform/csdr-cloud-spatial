# Geoscience Australia Sentinel-2 Coastal Ecosystems Calendar Year Collection 3
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

ace_app = typer.Typer()
logger = logging.getLogger(__name__)

# One STAC collection: https://explorer.dea.ga.gov.au/stac/collections/ga_s2_coastalecosystems_cyear_3_v1
# Containing two STAC items:
# 2021: https://explorer.dea.ga.gov.au/stac/collections/ga_s2_coastalecosystems_cyear_3_v1/items/830cf127-61c4-465a-92a5-8fc65188a9d7
# 2022: https://explorer.dea.ga.gov.au/stac/collections/ga_s2_coastalecosystems_cyear_3_v1/items/19eb5a11-986f-4e09-acc7-c9669bb7147a


# TODO: Generalise this to cli_dataset_stac.py.
async def run_index_aus_coastal_ecosystems(
    source_stac_url: str,
    stac_collection: str,
    target_location: str,
    target_filename: str,
    overwrite: bool = True,
) -> None:
    target_store = get_store_with_prefix_from_url(target_location, mkdir=True)

    target_filename = f"{target_filename}.parquet"

    target_url = f"{target_location}/{target_filename}"
    logger.info(f"Target URL: {target_url}")

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

    with suppress_rust_output():
        # Use rustac search_to to get all items from the ACE STAC collection and write to parquet
        # TODO: experiment with parquet_compression options for rustac write
        items = await search_to(
            target_filename,
            source_stac_url,
            collections=[stac_collection],
            store=target_store,
        )
    logger.info(
        f"Retrieved {items} items from STAC collection and wrote them to {target_filename}."
    )

    logger.info(f"Finished writing parquet file to {target_url}")


# Read all STAC items from ACE STAC Catalog and index them into a single STAC-Geoparquet file using rustac.
@ace_app.command("index")
def index_aus_coastal_ecosystems(
    source_stac_url: str = typer.Option(
        ...,
        help="S3 path to the bucket with ACE STAC documents.",
    ),
    stac_collection: str = typer.Option(
        ...,
        help="Name of the STAC collection.",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to store the indexed ACE parquet file.",
    ),
    target_filename: str = typer.Option(
        ...,
        help="Name of the target parquet file (without extension).",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logger.info("Starting ACE indexing process...")
    asyncio.run(
        run_index_aus_coastal_ecosystems(
            source_stac_url,
            stac_collection,
            target_location,
            target_filename,
            overwrite,
        )
    )
    logger.info("ACE indexing process completed.")
