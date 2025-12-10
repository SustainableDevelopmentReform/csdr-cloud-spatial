# Geoscience Australia Sentinel-2 Coastal Ecosystems Calendar Year Collection 3
import logging

import typer

ace_app = typer.Typer()

# Steps:
# 1. Index this STAC: # https://explorer.dea.ga.gov.au/stac/collections/ga_s2_coastalecosystems_cyear_3_v1
# Index writes for each of Intertidal, Mangrove, Saltmarsh, Intertidal Seagrass: To S3 as STAC-Geoparquet files.
# Just like Seagrass, just one step: Index.
# Just one datetime?

# https://data.dea.ga.gov.au/derivative/ga_s2_coastalecosystems_cyear_3_v1/AU/2021--P1Y/ga_s2_coastalecosystems_cyear_3_v1-0-0_AU_2021--P1Y_final_classification.tif
# https://data.dea.ga.gov.au/derivative/ga_s2_coastalecosystems_cyear_3_v1/AU/2022--P1Y/ga_s2_coastalecosystems_cyear_3_v1-0-0_AU_2022--P1Y_final_classification.tif

# mangrove_prob
# saltmarsh_prob
# seagrass_prob



# Set Rust logging environment variables BEFORE importing rustac
import asyncio
import logging

import typer
from rasterio import Env
from rustac import write

from csdr.io import (
    exists,
    get_stac_item_dicts_from_store,
    get_store_with_prefix_from_url,
)
from csdr.utils import suppress_rust_output


async def run_index_aus_coastal_ecosystems(
    source_location: str, target_location: str, overwrite: bool = True
) -> None:
    store = get_store_with_prefix_from_url(source_location, region="us-west-2")
    target_store = get_store_with_prefix_from_url(target_location)

    # for item in ["Intertidal", "Mangrove", "Saltmarsh", "Intertidal Seagrass"]:
    # logging.info(f"Indexing ACE class: {item}")
    # target_filename = f"ace_{item}.parquet"
    target_filename = "ace.parquet"

    target_url = f"{target_location}/{target_filename}"
    logging.info(f"Target URL: {target_url}")

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

    # Find all the the item STAC files
    with Env(AWS_REGION="us-west-2"):
        item_dicts = await get_stac_item_dicts_from_store(store)

    logging.info(
        f"Writing {len(item_dicts)} STAC items to parquet at {target_url}"
    )
    with suppress_rust_output():
        # TODO: experiment with parquet_compression options for rustac write
        await write(target_filename, item_dicts, store=target_store)

    logging.info(f"Finished writing parquet file to {target_url}")


# Read all STAC items from ACE bucket path and index them into a single STAC-Geoparquet file using rustac.
@ace_app.command("index")
def index_aus_coastal_ecosystems(
    source_location: str = typer.Option(
        ...,
        help="S3 path to the bucket with ACE STAC documents.",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to store the indexed ACE parquet file.",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logging.info("Starting ACE indexing process...")
    asyncio.run(run_index_aus_coastal_ecosystems(source_location, target_location, overwrite))
    logging.info("ACE indexing process completed.")
