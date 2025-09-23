# Set Rust logging environment variables BEFORE importing rustac
import asyncio
import json
import logging
import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZipFile

import typer
from loguru import logger
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import HTTPStore, LocalStore, S3Store
from odc.geo.cog import write_cog
from rio_stac import create_stac_item
from rioxarray import open_rasterio
from rustac import write

from csdr.io import exists, get_s3_prefix, get_store_for_url
from csdr.utils import suppress_rust_output

gmw_app = typer.Typer()


async def run_cache_gmw(
    source_url: str, target_location: str, target_path: str, target_zip_name: str
) -> None:
    logger.info(f"Caching GMW from {source_url} to {target_location}...")

    url = urlparse(source_url)

    source = HTTPStore(f"{url.scheme}://{url.netloc}")
    source_meta = source.head(url.path)

    size = source_meta.get("size", None)

    dest = None

    dest = get_store_for_url(target_location)
    target_zip_name = f"{target_path}/{target_zip_name}"

    if exists(dest, target_zip_name):
        dest_meta = dest.head(target_zip_name)
        if size is not None and "size" in dest_meta and dest_meta["size"] == size:
            logger.info(
                f"File already exists at target location with matching size of {size}. Skipping download."
            )
            return
        else:
            logger.info(
                f"File already exists at target location but size does not match (local: {size}, remote: {dest_meta['size']}). Re-downloading."
            )

    logger.info("Cached file doesn't exist, get it.")
    result = await dest.put_async(target_zip_name, source.get(url.path))
    logger.info(f"File cached successfully, downloaded {result} bytes")


@gmw_app.command("cache")
def cache_gmw(
    source_url: str = typer.Option(
        help="URL of the source GMW file to cache.",
        # default="https://zenodo.org/records/12756047/files/gmw_mng_2020_v4019_gtiff.zip?download=1",
        default="https://files.auspatious.com/gmwv3/gmw_mng_2020_v4019_gtiff.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (like './cache' or s3://files.auspatious.com/path/here) to store the cached GMW file.",
        default="./cache/gmw",
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
        run_cache_gmw(source_url, target_location, target_path, target_zip_name)
    )
    logger.info(
        f"GMW caching process completed. Cached to {target_location.rstrip('/')}/{target_zip_name}"
    )


async def process_single_file(
    name: str,
    zip_file: ZipFile,
    target_location: str,
    target_store: S3Store | LocalStore,
    overwrite: bool,
    semaphore: asyncio.Semaphore,
) -> None:
    """Process a single file from the zip archive."""
    async with semaphore:
        logger.info(f"Working on {name}...")
        out_key = name
        out_stac = name.replace(".tif", ".stac-item.json")

        # If S3, we need a S3 URI, otherwise, just a local path
        if target_location.startswith("s3://"):
            s3_url = urlparse(target_location)
            bucket = s3_url.netloc
            s3_prefix = s3_url.path.lstrip("/").rstrip("/")
            if s3_prefix is not None and s3_prefix != "":
                out_key = f"{s3_prefix}/{out_key}"
            target_uri = f"s3://{bucket}/{out_key}"
        else:
            target_uri = f"{target_location}/{out_key}"

        stac_uri = target_uri.replace(".tif", ".stac-item.json")

        # Check if the STAC doc exists, and skip if it does
        if exists(target_store, out_stac) and not overwrite:
            logger.info(f"STAC doc already exists for {name}, skipping.")
            return

        # Get the data from memory into a rasterio dataset
        data = open_rasterio(zip_file.open(name))

        # Write it as a COG
        cog_data = write_cog(data, ":mem:")
        await target_store.put_async(out_key, cog_data)

        # Create the STAC doc and write it
        stac_doc = create_stac_item(
            target_uri,
            input_datetime=datetime(2024, 1, 1),
            collection="gmw",
            id=name,
            asset_name="mangrove",
            with_proj=True,
            with_raster=True,
        )

        # Write STAC doc
        stac_key = out_key.replace(".tif", ".stac-item.json")
        stac_data = json.dumps(stac_doc.to_dict()).encode()
        await target_store.put_async(stac_key, stac_data)

        logger.info(f"Finished processing {name}. STAC doc is at {stac_uri}")


@gmw_app.command("extract")
def extract_gmw(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the extracted GMW files.",
        default="./cache/gmw",
    ),
    source_zip_name: str = typer.Option(
        help="Name of the zip file to extract the GMW data from.",
        default="gmw_mng_2020_v4019_gtiff.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the extracted GMW files.",
        default="./cache/gmw",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing files during extraction."
    ),
    max_concurrent: int = typer.Option(
        32, help="Maximum number of files to process concurrently."
    ),
) -> None:
    logger.info("Starting GMW extraction process...")
    asyncio.run(
        run_extract_gmw(
            source_location, source_zip_name, target_location, overwrite, max_concurrent
        )
    )


async def run_extract_gmw(
    source_location: str,
    source_zip_name: str,
    target_location: str,
    overwrite: bool,
    max_concurrent: int,
) -> None:
    """Async function to run the GMW extraction with parallel processing."""
    store = None
    s3_prefix = None
    if source_location.startswith("s3://"):
        s3_url = urlparse(source_location)
        bucket = s3_url.netloc
        store = S3Store(bucket, credential_provider=Boto3CredentialProvider())
        s3_prefix = s3_url.path.lstrip("/").rstrip("/")
        source_zip_name = f"{s3_prefix}/{source_zip_name}"
    else:
        store = LocalStore(prefix=Path(source_location), mkdir=True)

    source_exists = exists(store, source_zip_name)
    if not source_exists:
        logger.error(
            f"Source zip file does not exist at {source_location}/{source_zip_name}. Cannot extract."
        )
        raise typer.Exit(code=1)
    else:
        logger.info(
            f"Source zip file found at {source_location}/{source_zip_name}, proceeding with extraction."
        )

    target_store = get_store_for_url(target_location)

    # Open the zip file, and extract all files into memory
    # Load the file as bytes first
    logger.info("Loading data into memory")
    zip_bytes = BytesIO(store.get(source_zip_name).bytes())
    logger.info("Finished loading data")

    # Create semaphore to limit concurrent operations
    semaphore = asyncio.Semaphore(max_concurrent)

    with ZipFile(zip_bytes) as z:
        # Get list of TIF files to process
        tif_files = [name for name in z.namelist() if name.endswith(".tif")]
        logger.info(
            f"Found {len(tif_files)} TIF files to process with max {max_concurrent} concurrent operations."
        )

        # Create tasks for parallel processing
        tasks = [
            process_single_file(
                name, z, target_location, target_store, overwrite, semaphore
            )
            for name in tif_files
        ]

        # Execute all tasks concurrently
        await asyncio.gather(*tasks)

    logger.info("GMW extraction process completed.")


async def run_index_gmw(source_location: str, target_location: str) -> None:
    store = get_store_for_url(source_location)
    s3_prefix = None
    dest_s3_prefix = None
    if type(store) is S3Store:
        s3_prefix = get_s3_prefix(source_location)

    dest = get_store_for_url(target_location)
    dest_s3_prefix = None
    if type(dest) is S3Store:
        dest_s3_prefix = get_s3_prefix(target_location)

    # Find all the the GMW STAC files
    list_of_stac_files = []
    for batch in store.list(s3_prefix):
        for stac_file in batch:
            if stac_file["path"].endswith(".stac-item.json"):
                list_of_stac_files.append(stac_file)

    logger.info(f"Found {len(list_of_stac_files)} STAC items to index.")

    async def _fetch_item(store: S3Store, stac_file: dict) -> dict:
        obj = await store.get_async(stac_file["path"])
        data = BytesIO(obj.bytes())
        return json.load(data)

    item_dicts = await asyncio.gather(
        *(_fetch_item(store, stac_file) for stac_file in list_of_stac_files)
    )

    target = "gmw.parquet"
    if dest_s3_prefix is not None and dest_s3_prefix != "":
        target = f"{dest_s3_prefix}/{target}"

    # Multiple approaches to suppress rustac verbose logging
    os.environ["RUST_LOG"] = "off"  # Completely disable Rust logging
    os.environ["RUST_BACKTRACE"] = "0"  # Disable Rust backtraces too

    # Suppress any Python loggers that might be involved
    for logger_name in ["rustac", "arrow", "datafusion", "polars"]:
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)

    # Suppress stdout/stderr during rustac write to catch any remaining logs
    logger.info(f"Writing {len(item_dicts)} STAC items to parquet...")

    with suppress_rust_output():
        await write(target, item_dicts, store=dest)

    logger.info("Parquet write completed.")

    if target_location.startswith("s3://"):
        dest.buck
        logger.info(f"Finished writing to s3://{dest.config['bucket']}/{target}")
    else:
        logger.info(f"Finished writing to {source_location}/{target}")


@gmw_app.command("index")
def index_gmw(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to the GMW files.",
        default="./cache/gmw",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the indexed GMW parquet file.",
        default="./cache/gmw",
    ),
) -> None:
    logger.info("Starting GMW indexing process...")
    asyncio.run(run_index_gmw(source_location, target_location))
    logger.info("GMW indexing process completed.")
