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

dataset_stac_app = typer.Typer()


async def _run_index(
    source_stac_url: str,
    target_location: str,
    collection_name: str,
    overwrite: bool = True,
) -> None:
    target_store = get_store_with_prefix_from_url(target_location, mkdir=True)

    target_filename = f"{collection_name}.parquet"  # TODO: Is the collection name fine as the filename?

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
        # Use rustac search_to to get all items from the STAC collection and write to parquet
        # TODO: experiment with parquet_compression options for rustac write
        count_items = await search_to(
            target_filename,
            source_stac_url,
            collections=[collection_name],
            store=target_store,
        )
        if count_items == 0 or count_items is None:
            logging.error("No STAC items found, nothing to index.")
            exit(1)  # Exit with error code
    logging.info(
        f"Retrieved {count_items} items from STAC collection and wrote them to {target_filename}."
    )

    logging.info(f"Finished writing parquet file to {target_url}")


# Read all STAC items from a STAC API, or an S3 bucket of items, and index them into a single STAC-Geoparquet file using rustac.
@dataset_stac_app.command("index")
def index(
    source_stac_url: str = typer.Option(
        ...,
        help="URL to the STAC API or S3 path to the bucket with STAC documents.",
    ),
    collection_name: str = typer.Option(
        ...,
        help="Name of the STAC collection to index.",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to store the indexed STAC-Geoparquet file.",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logging.info("Starting STAC indexing process...")
    asyncio.run(
        _run_index(source_stac_url, target_location, collection_name, overwrite)
    )
    logging.info("STAC indexing process completed.")
