import asyncio

import typer
from loguru import logger
from obstore.store import S3Store

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
    # downloads the EEZ zip file from source_url and stores it at target_location
    # source url can be s3://, http://, or local file path
    # target location can be s3:// or local file path
    logger.info(f"Caching EEZ from {source_url} to {target_location}...")
    target_location = target_location.rstrip("/")
    store = get_store_for_url(source_url)
    source_name_path = get_dataset_name_from_url(store, source_url)
    size = get_file_info(store, source_name_path).get("size", None)
    file_name = get_dataset_name_from_url(store, source_url, keep_path=False)
    target_store = get_store_for_url(target_location)
    target_filename = file_name

    if type(target_store) is S3Store:
        # S3Store needs the full path including prefix
        path = get_prefix(target_location)
        if path is not None:
            target_filename = f"{path}/{target_filename}"
    target_url = get_url_from_store_filename(target_store, target_filename)

    if exists(target_store, target_filename):
        if not overwrite:
            logger.info("File already exists at target location, skipping download.")
            raise typer.Exit(code=0)  # Exit successfully, nothing to do
        else:
            dest_meta = target_store.head(target_filename)
            if size is not None and "size" in dest_meta and dest_meta["size"] == size:
                logger.info(
                    f"Overwrite is on but file already exists at target location with matching size of {size}. Skipping download."
                )
                raise typer.Exit(code=0)  # Exit successfully, nothing to do
            else:
                logger.info(
                    f"Overwrite is on. File already exists at target location but size does not match (local: {size}, remote: {dest_meta['size']}). Re-downloading."
                )

    logger.info(f"Downloading {file_name} from {source_url} to {target_url}...")
    await target_store.put_async(target_filename, store.get(file_name))

    return f"{target_url}"


@eez_app.command("cache")
def cache_eez(
    source_url: str = typer.Option(
        help="URL of the source EEZ file to cache.",
        default="https://files.auspatious.com/unsw/EEZ_land_union_v4_202410.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (like './cache' or s3://files.auspatious.com/path/here) to store the cached EEZ file.",
        default="./cache/eez",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing zip file if it exists."
    ),
) -> None:
    logger.info("Starting EEZ caching process...")

    result_path = asyncio.run(run_cache_eez(source_url, target_location, overwrite))
    logger.info(f"EEZ caching process completed. Cached to {result_path}")
