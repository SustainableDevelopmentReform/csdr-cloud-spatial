import asyncio
import logging

import typer

from csdr.io import (
    exists,
    get_file_info,
    get_file_name_from_url,
    get_store_with_prefix_from_url,
)

eez_app = typer.Typer()


async def run_cache_eez(
    source_url: str,
    target_location: str,
    overwrite: bool,
) -> str:
    # Downloads the EEZ zip file from source_url and stores it at target_location
    # Source url can be s3://, http://, or local file path
    # Target location can be s3:// or local file path
    logging.info(f"Caching EEZ from {source_url} to {target_location}...")
    target_location = target_location.rstrip("/") # Remove trailing slash if present
    store = get_store_with_prefix_from_url(source_url)
    source_name = get_file_name_from_url(source_url)
    size = get_file_info(store, source_name).get("size", None)
    target_filename = get_file_name_from_url(source_url)
    target_store = get_store_with_prefix_from_url(target_location)

    if exists(target_store, target_filename):
        if not overwrite:
            logging.info("File already exists at target location, skipping download.")
            raise typer.Exit(code=0)  # Exit successfully, nothing to do
        else:
            dest_meta = target_store.head(target_filename)
            if size is not None and "size" in dest_meta and dest_meta["size"] == size:
                logging.info(
                    f"Overwrite is on but file already exists at target location with matching size of {size}. Skipping download."
                )
                raise typer.Exit(code=0)  # Exit successfully, nothing to do
            else:
                logging.info(
                    f"Overwrite is on. File already exists at target location but size does not match (local: {size}, remote: {dest_meta['size']}). Re-downloading."
                )

    logging.info(f"Downloading {target_filename} from {source_url} to {target_location}...")
    await target_store.put_async(target_filename, store.get(target_filename))

    return target_location


@eez_app.command("cache")
def cache_eez(
    source_url: str = typer.Option(
        help="URL of the source EEZ file to cache.",
        default="https://files.auspatious.com/unsw/EEZ_land_union_v4_202410.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (like './cache/eez-v4/0-0-1/raw' or s3://files.auspatious.com/csdr/eez-v4/0-0-1/raw) to store the cached EEZ file.",
        default="./cache/eez-v4/0-0-1/raw",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing zip file if it exists."
    ),
) -> None:
    logging.info("Starting EEZ caching process...")

    result_path = asyncio.run(run_cache_eez(source_url, target_location, overwrite))
    logging.info(f"EEZ caching process completed. Cached to {result_path}")
