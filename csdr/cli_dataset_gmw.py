import asyncio
import json
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

from csdr.utils import exists

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


@gmw_app.command("cache")
def cache_gmw(
    source_url: str = typer.Option(
        help="URL of the source GMW file to cache.",
        # default="https://zenodo.org/records/12756047/files/gmw_mng_2020_v4019_gtiff.zip?download=1",
        default="https://files.auspatious.com/gmwv3/gmw_mng_2020_v4019_gtiff.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (like './cache' or s3://files.auspatious.com/path/here) to store the cached GMW file.",
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
        run_cache_gmw(source_url, target_location, target_path, target_zip_name)
    )
    logger.info(
        f"GMW caching process completed. Cached to {target_location.rstrip('/')}/{target_zip_name}"
    )


@gmw_app.command("extract")
def extract_gmw(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the extracted GMW files.",
        default="./cache",
    ),
    source_zip_name: str = typer.Option(
        help="Name of the zip file to extract the GMW data from.",
        default="gmw_mng_2020_v4019_gtiff.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the extracted GMW files.",
        default="./cache",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing files during extraction."
    ),
    overwrite_stac: bool = typer.Option(
        True, help="Replace existing STAC files during extraction."
    ),
) -> None:
    logger.info("Starting GMW extraction process...")

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

    target_store = None
    if target_location.startswith("s3://"):
        s3_url = urlparse(target_location)
        bucket = s3_url.netloc
        target_store = S3Store(bucket, credential_provider=Boto3CredentialProvider())
    else:
        target_store = LocalStore(prefix=Path(target_location), mkdir=True)

    # Open the zip file, and extract all files into memory
    # Load the file as bytes first
    logger.info("Loading data into memory")
    zip_bytes = BytesIO(store.get(source_zip_name).bytes())
    logger.info("Finished loading data")

    with ZipFile(zip_bytes) as z:
        for name in z.namelist():
            logger.info(f"Converting {name}...")
            # Get the data from memory into a rasterio dataset
            data = open_rasterio(z.open(name))
            out_key = name
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

            if not exists(target_store, out_key) or overwrite:
                target_store.put(f"{out_key}", write_cog(data, ":mem:"))
                logger.info(f"Converted to COG and wrote to {target_uri}")
            else:
                logger.info("File already exists, skipping.")

            if (
                not exists(target_store, out_key.replace(".tif", ".stac-item.json"))
                or overwrite_stac
            ):
                stac_doc = create_stac_item(
                    target_uri,
                    input_datetime=datetime(2024, 1, 1),
                    collection="gmw",
                    id=name,
                    asset_name="mangrove",
                    with_proj=True,
                    with_raster=True,
                )
                target_store.put(
                    out_key.replace(".tif", ".stac-item.json"),
                    json.dumps(stac_doc.to_dict(), indent=2).encode("utf-8"),
                    attributes={"Content-Type": "application/json"},
                )
            else:
                logger.info("STAC file already exists, skipping.")

            if target_location.startswith("s3://"):
                logger.info(
                    f"Finished. STAC doc is at {target_uri.replace('.tif', '.stac-item.json')}"
                )

    logger.info("GMW extraction process completed.")


async def run_index_gmw(source_location: str) -> None:
    store = None
    s3_prefix = None
    bucket = None
    if source_location.startswith("s3://"):
        s3_url = urlparse(source_location)
        bucket = s3_url.netloc
        store = S3Store(bucket, credential_provider=Boto3CredentialProvider())
        s3_prefix = s3_url.path.lstrip("/").rstrip("/")
    else:
        store = LocalStore(prefix=Path(source_location), mkdir=True)

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
    if s3_prefix is not None and s3_prefix != "":
        target = f"{s3_prefix}/{target}"

    await write(target, item_dicts, store=store)

    if source_location.startswith("s3://"):
        logger.info(f"Finished writing to s3://{bucket}/{target}")
    else:
        logger.info(f"Finished writing to {source_location}/{target}")


@gmw_app.command("index")
def index_gmw(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to the GMW files.",
        default="./cache",
    ),
) -> None:
    logger.info("Starting GMW indexing process...")
    asyncio.run(run_index_gmw(source_location))
    logger.info("GMW indexing process completed.")
