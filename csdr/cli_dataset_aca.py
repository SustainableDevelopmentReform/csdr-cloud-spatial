# Set Rust logging environment variables BEFORE importing rustac
import asyncio
import json
import sys
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import urlparse
from zipfile import ZipFile

import typer
import xarray as xr
from loguru import logger
from obstore.store import HTTPStore, LocalStore, S3Store
from odc.geo.cog import write_cog
from rio_stac import create_stac_item
from rioxarray import open_rasterio
from rustac import write

from csdr.io import (
    exists,
    get_prefix,
    get_stac_item_dicts_from_store,
    get_store_for_url,
    get_url_from_store_filename,
)
from csdr.utils import suppress_rust_output

gmw_app = typer.Typer()


"""
async def cache_single_source(
    source_url: str,
    target_location: str,
    target_path: str,
    target_zip_name: str,
    overwrite: bool,
    semaphore: asyncio.Semaphore,
) -> str:
    # Process a single file from source.
    async with semaphore:
        logger.info(f"Caching GMW from {source_url} to {target_location}...")

        url = urlparse(source_url)

        source = HTTPStore(f"{url.scheme}://{url.netloc}")

        # Must check this file exists before proceeding
        source_exists = exists(source, url.path)
        if not source_exists:
            logger.error(f"Source file does not exist at {source_url}. Cannot extract.")
            return
        else:
            logger.info(
                f"Source file found at {source_url}, proceeding with extraction."
            )

        source_meta = source.head(url.path)
        size = source_meta.get("size", None)

        dest = None
        dest = get_store_for_url(target_location)
        target_zip_name = (
            f"{target_path}/{target_zip_name}"
            if target_path is not None
            else target_zip_name
        )

        target_url = get_url_from_store_filename(dest, target_zip_name)
        logger.info(f"Target URL for caching is {target_url}")

        if exists(dest, target_zip_name) and not overwrite:
            dest_meta = dest.head(target_zip_name)
            if size is not None and "size" in dest_meta and dest_meta["size"] == size:
                logger.info(
                    f"File already exists at target location with matching size of {size}. Skipping download."
                )
                return f"{target_location}{target_zip_name}"
            else:
                logger.info(
                    f"File already exists at target location but size does not match (local: {size}, remote: {dest_meta['size']}). Re-downloading."
                )

        if overwrite:
            logger.info(f"Overwrite is enabled, re-downloading {source_url} file.")
        else:
            logger.info(
                f"File {target_zip_name} does not exist at target location, downloading."
            )
        _ = await dest.put_async(target_zip_name, source.get(url.path))
        logger.info(f"File cached successfully, downloaded to {target_url}")

        return f"{target_location}{target_zip_name}"



async def run_cache_gmw(
    source_locations: list[str],
    target_location: str,
    target_path: str,
    overwrite: bool,
    max_concurrent: int,
    out_file: str,
) -> None:
    # Async function to run the GMW cache with parallel processing.

    # Create semaphore to limit concurrent operations
    semaphore = asyncio.Semaphore(max_concurrent)

    # Create tasks for parallel processing
    tasks = [
        cache_single_source(
            source_location,
            target_location,
            target_path,
            (source_location.rsplit("/", 1)[-1].rsplit("?", 1)[0]),
            overwrite,
            semaphore,
        )
        for source_location in source_locations
    ]

    # Execute all tasks concurrently
    results = await asyncio.gather(*tasks)

    # Strip out nulls if source file was not found
    results = [file for file in results if file is not None]

    if out_file is not None:
        with open(out_file, "w") as f:
            json.dump(results, f, indent=4)
        logger.info(f"Wrote target files to {out_file}")
    else:
        sys.stdout.write(json.dumps(results, indent=4))


@gmw_app.command("cache")
def cache_gmw(
    source_location: str = typer.Option(
        help="Location of the source GMW file/s to cache.",
        # default="https://zenodo.org/records/12756047/files/gmw_mng_2020_v4019_gtiff.zip?download=1",
        # default="https://files.auspatious.com/gmwv3/gmw_mng_2020_v4019_gtiff.zip",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (like './cache' or s3://files.auspatious.com/path/here) to store the cached GMW file.",
        default="./cache/gmw",
    ),
    overwrite: bool = typer.Option(
        False, help="Replace existing files during caching."
    ),
    max_concurrent: int = typer.Option(
        32, help="Maximum number of source files to process concurrently."
    ),
    out_file: str = typer.Option(
        None,
        help="Tempfile to write list of target locations (otherwise print to console)",
    ),
) -> None:
    logger.info("Starting GMW caching process...")
    target_path = None
    if target_location.startswith("s3://"):
        target_path = urlparse(target_location).path.lstrip("/").rstrip("/")

    # Get list of source_locations
    source_locations = source_location.split(",")

    asyncio.run(
        run_cache_gmw(
            source_locations,
            target_location,
            target_path,
            overwrite,
            max_concurrent,
            out_file,
        )
    )
    logger.info(
        f"GMW caching process completed. Cached to {target_location.rstrip('/')}"
    )


async def process_single_file(
    name: str,
    zip_file: ZipFile,
    target_location: str,
    target_store: S3Store | LocalStore,
    overwrite: bool,
    semaphore: asyncio.Semaphore,
) -> None:
    # Process a single file from the zip archive.
    async with semaphore:
        # logger.info(f"Working on {name}...")
        out_key = name
        out_stac = name.replace(".tif", ".stac-item.json")

        # If S3, we need a S3 URI, otherwise, just a local path
        if type(target_store) is S3Store:
            s3_prefix = get_prefix(target_location)
            if s3_prefix is not None:
                out_key = f"{s3_prefix}/{out_key}"
                out_stac = f"{s3_prefix}/{out_stac}"
            target_uri = f"s3://{target_store.config['bucket']}/{out_key}"
        else:
            target_uri = f"{target_location}/{out_key}"

        stac_uri = target_uri.replace(".tif", ".stac-item.json")
        if exists(target_store, out_stac) and not overwrite:
            logger.info(f"STAC doc already exists for {stac_uri}, skipping.")
            return
        else:
            if overwrite:
                logger.info(f"Overwrite is enabled, re-processing {stac_uri}.")
            else:
                logger.info(f"STAC does not exist for {stac_uri}, processing.")

        # Get the data from memory into a rasterio dataset
        data = open_rasterio(zip_file.open(name))

        if type(data) is not xr.DataArray:  # skip
            logger.info(
                f"Skipping file {name}. Expecting xarray.DataArray but got {type(data)} instead."
            )
        else:
            # Write it as a COG
            cog_data = write_cog(data, ":mem:")
            await target_store.put_async(out_key, cog_data)

            # Create the STAC doc and write it
            # Let's see which version of GMW we have
            if "_v3" in name:
                start_datetime = datetime(int(name[-11:-7]), 1, 1).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )
                mid_datetime = datetime(int(name[-11:-7]), 7, 2)
                end_datetime = datetime(int(name[-11:-7]), 12, 31).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )
            elif "_v4" in name:
                start_datetime = datetime(2020, 1, 1).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                mid_datetime = datetime(2020, 7, 2)
                end_datetime = datetime(2020, 12, 31).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            else:
                start_datetime = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                mid_datetime = datetime.now(UTC)
                end_datetime = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            stac_doc = create_stac_item(
                target_uri,
                input_datetime=mid_datetime,
                collection="gmw",
                properties={
                    "start_datetime": start_datetime,
                    "end_datetime": end_datetime,
                },
                id=name,
                asset_name="mangrove",
                with_proj=True,
                with_raster=True,
            )

            # Write STAC doc
            stac_data = json.dumps(stac_doc.to_dict()).encode()
            await target_store.put_async(out_stac, stac_data)

            logger.info(f"Finished processing {name}. STAC doc is at {stac_uri}")


async def run_extract_gmw(
    source_location: str,
    source_zip_name: str,
    target_location: str,
    overwrite: bool,
    max_concurrent: int,
) -> None:
    # Async function to run the GMW extraction with parallel processing.
    store = None
    s3_prefix = None

    # Cleanup...
    source_location = source_location.rstrip("/")

    store = get_store_for_url(source_location)

    if type(store) is S3Store:
        s3_prefix = get_prefix(source_location)
        if s3_prefix is not None:
            source_zip_name = f"{s3_prefix}/{source_zip_name}"

    logger.info(f"Checking for source zip file at path {source_zip_name}...")
    if type(store) is S3Store:
        logger.info(
            f"Store is S3Store with bucket {store.config['bucket']} and prefix {s3_prefix}"
        )

    source_exists = exists(store, source_zip_name)
    if not source_exists:
        logger.error(
            f"Source zip file does not exist at {source_location}. Cannot extract."
        )
        raise typer.Exit(code=1)
    else:
        logger.info(
            f"Source zip file found at {source_location}, proceeding with extraction."
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


async def run_index_gmw(
    source_location: str, target_location: str, overwrite: bool = True
) -> None:
    store = get_store_for_url(source_location)
    s3_prefix = None
    if type(store) is S3Store:
        s3_prefix = get_prefix(source_location)

    dest = get_store_for_url(target_location)

    out_filename = "gmw.parquet"
    dest_s3_prefix = None

    if type(dest) is S3Store:
        dest_s3_prefix = get_prefix(target_location)
        if dest_s3_prefix is not None:
            out_filename = f"{dest_s3_prefix}/{out_filename}"

    # Check for existing geoparquet file
    if exists(dest, out_filename) and not overwrite:
        logger.info(
            f"Parquet file already exists at {out_filename}, skipping indexing."
        )
        return
    else:
        if overwrite:
            logger.info("Overwrite is enabled, re-indexing GMW.")
        else:
            logger.info("Parquet file does not exist, proceeding with indexing.")

    # Find all the the GMW STAC files
    item_dicts = await get_stac_item_dicts_from_store(store, s3_prefix)

    result_location = get_url_from_store_filename(dest, out_filename)

    logger.info(f"Writing {len(item_dicts)} STAC items to parquet at {result_location}")
    with suppress_rust_output():
        await write(out_filename, item_dicts, store=dest)

    logger.info(f"Parquet write completed, wrote to {result_location}")


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
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logger.info("Starting GMW indexing process...")
    asyncio.run(run_index_gmw(source_location, target_location, overwrite))
    logger.info("GMW indexing process completed.")
"""

async def run_merge_aca(
    source_location: str, target_location: str, overwrite: bool = True
) -> None:
    store = get_store_for_url(source_location)
    s3_prefix = None
    if type(store) is S3Store:
        s3_prefix = get_prefix(source_location)

    dest = get_store_for_url(target_location)

    out_filename = "aca.parquet"
    dest_s3_prefix = None

    if type(dest) is S3Store:
        dest_s3_prefix = get_prefix(target_location)
        if dest_s3_prefix is not None:
            out_filename = f"{dest_s3_prefix}/{out_filename}"

    # Check for existing geoparquet file
    if exists(dest, out_filename) and not overwrite:
        logger.info(
            f"Parquet file already exists at {out_filename}, skipping indexing."
        )
        return
    else:
        if overwrite:
            logger.info("Overwrite is enabled, re-merging ACA.")
        else:
            logger.info("Parquet file does not exist, proceeding with indexing.")

    # now do the merging
    logger.info("merging")


@aca_app.command("merge")
def merge_aca(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to the ACA files.",
        default="./cache/aca",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the merged ACA parquet file.",
        default="./cache/aca",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logger.info("Starting ACA merging process...")
    asyncio.run(run_merge_aca(source_location, target_location, overwrite))
    logger.info("ACA merging process completed.")

