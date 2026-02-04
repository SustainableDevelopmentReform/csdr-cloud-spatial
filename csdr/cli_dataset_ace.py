# Geoscience Australia Sentinel-2 Coastal Ecosystems Calendar Year Collection 3
# Set Rust logging environment variables BEFORE importing rustac
import asyncio
import logging

import typer
from rustac import search

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    stac_items_to_arrow,
    write_arrow_to_parquet,
)
from csdr.utils import suppress_rust_output

ace_app = typer.Typer()

# One STAC collection: https://explorer.dea.ga.gov.au/stac/collections/ga_s2_coastalecosystems_cyear_3_v1
# Containing two STAC items:
# 2021: https://explorer.dea.ga.gov.au/stac/collections/ga_s2_coastalecosystems_cyear_3_v1/items/830cf127-61c4-465a-92a5-8fc65188a9d7
# 2022: https://explorer.dea.ga.gov.au/stac/collections/ga_s2_coastalecosystems_cyear_3_v1/items/19eb5a11-986f-4e09-acc7-c9669bb7147a

async def run_index_aus_coastal_ecosystems(
    source_stac_url: str, target_location: str, overwrite: bool = True
) -> None:
    target_store = get_store_with_prefix_from_url(target_location, mkdir=True)

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

    with suppress_rust_output():
        # Use rustac search to get all items from the ACE STAC collection and write to parquet (arrow)

        items = await search(
            source_stac_url,
            collections=["ga_s2_coastalecosystems_cyear_3_v1"],
        )
        count_items = len(items)

        arrow_table = stac_items_to_arrow(items)

        logging.info(f"Writing {count_items} STAC items to parquet (arrow) at {target_url}")

        write_arrow_to_parquet(arrow_table, target_store, target_filename)
    logging.info(f"Wrote {count_items} items from STAC collection and wrote them as STAC-Geoparquet (arrow) to {target_filename}.")


# Read all STAC items from ACE STAC Catalog and index them into a single STAC-Geoparquet file using rustac.
@ace_app.command("index")
def index_aus_coastal_ecosystems(
    source_stac_url: str = typer.Option(
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
    asyncio.run(run_index_aus_coastal_ecosystems(source_stac_url, target_location, overwrite))
    logging.info("ACE indexing process completed.")
