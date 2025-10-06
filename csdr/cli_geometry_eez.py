import asyncio

import typer
from loguru import logger

from csdr.io import (
    exists,
    get_dataset_name_from_url,
    get_file_info,
    get_prefix,
    get_store_for_url,
    get_url_from_store_filename,
)

eez_app = typer.Typer()


async def run_cache_eez(
    source_url: str,
    target_location: str,
    overwrite: bool,
) -> None:
    logger.info(f"Caching EEZ from {source_url} to {target_location}...")
    target_location = target_location.rstrip("/")

    source = get_store_for_url(source_url)
    name = get_dataset_name_from_url(source, source_url)
    size = get_file_info(source, name).get("size", None)

    dest = get_store_for_url(target_location)
    name = get_dataset_name_from_url(source, source_url, keep_path=False)
    dest_name = f"{get_prefix(target_location)}/{name}"

    dest_url = get_url_from_store_filename(dest, dest_name)

    if exists(dest, dest_name):
        if not overwrite:
            logger.info("File already exists at target location, skipping download.")
            raise typer.Exit(code=0)  # Exit successfully, nothing to do
        else:
            dest_meta = dest.head(dest_name)
            if size is not None and "size" in dest_meta and dest_meta["size"] == size:
                logger.info(
                    f"File already exists at target location with matching size of {size}. Skipping download."
                )
                raise typer.Exit(code=0)  # Exit successfully, nothing to do
            else:
                logger.info(
                    f"File already exists at target location but size does not match (local: {size}, remote: {dest_meta['size']}). Re-downloading."
                )

    logger.info(f"Downloading {name} from {source_url} to {dest_url}...")
    await dest.put_async(dest_name, source.get(name))

    return f"{dest_url}"


@eez_app.command("cache")
def cache_eez(
    source_url: str = typer.Option(
        help="URL of the source EEZ file to cache.",
        default="https://files.auspatious.com/unsw/EEZ_land_union_v4_202410.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (like './cache' or s3://files.auspatious.com/path/here) to store the cached EEZ file.",
        default="cache/eez",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing zip file if it exists."
    ),
) -> None:
    logger.info("Starting EEZ caching process...")

    result_path = asyncio.run(run_cache_eez(source_url, target_location, overwrite))
    logger.info(f"EEZ caching process completed. Cached to {result_path}")
