import asyncio
from pathlib import Path
from urllib.parse import urlparse

import typer
from loguru import logger
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import HTTPStore, LocalStore, S3Store

from csdr.utils import exists

eez_app = typer.Typer()


async def run_cache_eez(
    source_url: str, target_location: str, target_path: str, target_zip_name: str
) -> None:
    logger.info(f"Caching EEZ from {source_url} to {target_location}...")

    url = urlparse(source_url)

    source = HTTPStore(f"{url.scheme}://{url.netloc}")
    source_meta = source.head(url.path)

    size = source_meta.get("size", None)

    dest = None
    if target_location.startswith("s3://"):
        s3_url = urlparse(target_location)
        bucket = s3_url.netloc
        dest = S3Store(bucket, credential_provider=Boto3CredentialProvider())
    else:
        dest = LocalStore(prefix=Path(target_location), mkdir=True)

    download = True
    target_zip_name = f"{target_path}/{target_zip_name}"

    if exists(dest, target_zip_name):
        dest_meta = dest.head(target_zip_name)
        if size is not None and "size" in dest_meta and dest_meta["size"] == size:
            logger.info(
                f"File already exists at target location with matching size of {size}. Skipping download."
            )
            download = False
        else:
            logger.info(
                f"File already exists at target location but size does not match (local: {size}, remote: {dest_meta['size']}). Re-downloading."
            )

    if download:
        logger.info("Cached file doesn't exist, get it.")
        result = await dest.put_async(target_zip_name, source.get(url.path))
        logger.info(f"File cached successfully, downloaded {result} bytes")


@eez_app.command("cache")
def cache_eez(
    source_url: str = typer.Option(
        help="URL of the source EEZ file to cache.",
        default="https://files.auspatious.com/unsw/EEZ_land_union_v4_202410.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (like './cache' or s3://files.auspatious.com/path/here) to store the cached EEZ file.",
        default="./cache",
    ),
    target_zip_name: str = typer.Option(
        help="Name of the zip file to save the GMW data as.",
        default="gmw_mng_2020_v4019_gtiff.zip",
    ),
) -> None:
    logger.info("Starting GMW caching process...")
    target_path = ""
    if target_location.startswith("s3://"):
        target_path = urlparse(target_location).path.lstrip("/").rstrip("/")

    asyncio.run(
        run_cache_eez(source_url, target_location, target_path, target_zip_name)
    )
    logger.info(
        f"EEZ caching process completed. Cached to {target_location.rstrip('/')}/{target_zip_name}"
    )
