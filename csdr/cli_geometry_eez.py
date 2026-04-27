import asyncio
import logging

import typer

from csdr.io import (
    exists,
    get_file_info,
    get_store_with_prefix_from_url,
    split_path_and_file_name_from_url,
)
from csdr.provenance import write_step

eez_app = typer.Typer()
logger = logging.getLogger(__name__)


async def run_cache_eez(
    source_url: str,
    target_location: str,
    overwrite: bool,
) -> str:
    # Downloads the EEZ zip file from source_url and stores it at target_location
    # Source url can be s3://, http://, or local file path
    # Target location can be s3:// or local file path
    logger.info(f"Caching EEZ from {source_url} to {target_location}...")
    target_location = target_location.rstrip("/")
    source_path, source_name = split_path_and_file_name_from_url(source_url)
    source_store = get_store_with_prefix_from_url(source_path)
    size = get_file_info(source_store, source_name).get("size", None)
    target_path = target_location  # This is the path, there is no file name
    target_file_name = source_name
    target_store = get_store_with_prefix_from_url(target_path)

    if exists(target_store, target_file_name):
        if not overwrite:
            logger.info("File already exists at target location, skipping download.")
            raise typer.Exit(code=0)  # Exit successfully, nothing to do
        else:
            dest_meta = target_store.head(target_file_name)
            if size is not None and "size" in dest_meta and dest_meta["size"] == size:
                logger.info(
                    f"Overwrite is on but file already exists at target location with matching size of {size}. Skipping download."
                )
                raise typer.Exit(code=0)  # Exit successfully, nothing to do
            else:
                logger.info(
                    f"Overwrite is on. File already exists at target location but size does not match (local: {size}, remote: {dest_meta['size']}). Re-downloading."
                )

    logger.info(
        f"Downloading {target_file_name} from {source_url} to {target_location}..."
    )
    await target_store.put_async(target_file_name, source_store.get(target_file_name))

    return target_location


@eez_app.command("cache")
def cache_eez(
    source_url: str = typer.Option(
        help="URL of the source EEZ file to cache.",
        default="https://files.auspatious.com/unsw/EEZ_land_union_v4_202410.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (like './cache/geometries/eez-v4/0-0-1/raw' or s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/raw) to store the cached EEZ file.",
        default="./cache/geometries/eez-v4/0-0-1/raw",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing zip file if it exists."
    ),
) -> None:
    logger.info("Starting EEZ caching process...")

    result_path = asyncio.run(run_cache_eez(source_url, target_location, overwrite))
    logger.info(f"EEZ caching process completed. Cached to {result_path}")
    write_step(
        label="Cache EEZ zip file from source",
        inputs={"source_url": source_url},
        outputs={"target_location": target_location},
    )
