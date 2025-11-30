# Set Rust logging environment variables BEFORE importing rustac
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZipFile

import typer
import xarray as xr
from obstore.store import HTTPStore, LocalStore, S3Store
from odc.geo.cog import write_cog
from rio_stac import create_stac_item
from rioxarray import open_rasterio
from rustac import write

from csdr.io import (
    exists,
    get_stac_item_dicts_from_store,
    get_store_with_prefix_from_url,
)
from csdr.utils import suppress_rust_output

gmw_app = typer.Typer()


async def cache_single_source(
    source_url: str,
    target_location: str,
    target_path: str,
    target_zip_name: str,
    overwrite: bool,
    semaphore: asyncio.Semaphore,
) -> str:
    """Process a single file from source."""
    async with semaphore:
        logging.info(f"Caching GMW from {source_url} to {target_location}...")

        url = urlparse(source_url)

        source = HTTPStore(f"{url.scheme}://{url.netloc}")

        # Must check this file exists before proceeding
        source_exists = exists(source, url.path)
        if not source_exists:
            logging.error(f"Source file does not exist at {source_url}. Cannot extract.")
            return
        else:
            logging.info(
                f"Source file found at {source_url}, proceeding with extraction."
            )

        source_meta = source.head(url.path)
        size = source_meta.get("size", None)

        target_store = None
        target_store = get_store_with_prefix_from_url(target_location)
        target_zip_name = (
            f"{target_path}/{target_zip_name}"
            if target_path is not None
            else target_zip_name
        )
        target_url = f"{target_location}/{target_zip_name}"
        logging.info(f"Target URL for caching is {target_url}")

        if exists(target_store, target_zip_name) and not overwrite:
            dest_meta = target_store.head(target_zip_name)
            if size is not None and "size" in dest_meta and dest_meta["size"] == size:
                logging.info(
                    f"File already exists at target location with matching size of {size}. Skipping download."
                )
                return f"{target_location}{target_zip_name}"
            else:
                logging.info(
                    f"File already exists at target location but size does not match (local: {size}, remote: {dest_meta['size']}). Re-downloading."
                )

        if overwrite:
            logging.info(f"Overwrite is enabled, re-downloading {source_url} file.")
        else:
            logging.info(
                f"File {target_zip_name} does not exist at target location, downloading."
            )
        _ = await target_store.put_async(target_zip_name, source.get(url.path))
        logging.info(f"File cached successfully, downloaded to {target_url}")

        return f"{target_location}/{target_zip_name}"


async def run_cache_gmw(
    source_locations: list[str],
    target_location: str,
    target_path: str,
    overwrite: bool,
    max_concurrent: int,
    out_file: str,
) -> None:
    """Async function to run the GMW cache with parallel processing."""

    # Create semaphore to limit concurrent operations
    semaphore = asyncio.Semaphore(max_concurrent)

    # Create tasks for parallel processing
    tasks = [
        cache_single_source(
            source_location,
            target_location.rstrip("/"),
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
        logging.info(f"Wrote target files to {out_file}")
    else:
        sys.stdout.write(json.dumps(results, indent=4))


# Takes a list of source locations of one or more zip files containing many geotiff files
# Writes these to the target location. Also writes a temporary file with list of target locations for the workflow to read.
@gmw_app.command("cache")
def cache_gmw(
    source_locations: str = typer.Option(
        ...,
        help="Location of the source GMW file/s to cache.",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (like './cache' or s3://csdr-public-dev/datasets/gmw-v4) to store the cached GMW file/s.",
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
    logging.info("Starting GMW caching process...")
    target_path = None
    # Handle S3 target path
    if target_location.startswith("s3://"):
        target_path = urlparse(target_location).path.lstrip("/").rstrip("/")

    # Get list of source_locations
    source_locations = source_locations.split(",")

    logging.info("Starting async GMW caching process...")

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
    logging.info(
        f"GMW caching process completed. Cached to {target_location.rstrip('/')}"
    )


async def process_single_file(
    out_cog_name: str,
    zip_file: ZipFile,
    target_location: str,
    target_store: S3Store | LocalStore,
    overwrite: bool,
    semaphore: asyncio.Semaphore,
) -> None:
    """Process a single file from the zip archive."""
    async with semaphore:
        out_cog_url = f"{target_location}/{out_cog_name}"
        out_stac_name = out_cog_name.replace('.tif', '.stac-item.json')
        out_stac_url = f"{target_location}/{out_stac_name}"

        if exists(target_store, out_stac_name) and not overwrite:
            logging.info(f"STAC doc already exists for {out_stac_url}, skipping.")
            return
        else:
            if overwrite:
                logging.info(f"Overwrite is enabled, re-processing {out_stac_url}.")
            else:
                logging.info(f"STAC does not exist for {out_stac_url}, processing.")

        # Get the data from memory into a rasterio dataset
        data = open_rasterio(zip_file.open(out_cog_name))

        if type(data) is not xr.DataArray:  # skip
            logging.info(
                f"Skipping file {out_cog_name}. Expecting xarray.DataArray but got {type(data)} instead."
            )
        else:
            # Write it as a COG
            cog_data = write_cog(data, ":mem:")
            await target_store.put_async(out_cog_name, cog_data)

            # Create the STAC doc and write it
            # Let's see which version of GMW we have
            if "_v3" in out_cog_name:
                start_datetime = datetime(int(out_cog_name[-11:-7]), 1, 1).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )
                mid_datetime = datetime(int(out_cog_name[-11:-7]), 7, 2)
                end_datetime = datetime(int(out_cog_name[-11:-7]), 12, 31).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )
            elif "_v4" in out_cog_name:
                start_datetime = datetime(2020, 1, 1).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                mid_datetime = datetime(2020, 7, 2)
                end_datetime = datetime(2020, 12, 31).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            # TODO: What does this else cover? We are just doing v3 and v4 aren't we?
            else:
                start_datetime = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                mid_datetime = datetime.now(UTC)
                end_datetime = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

            stac_doc = create_stac_item(
                out_cog_url, # Absolute URL to the COG file
                input_datetime=mid_datetime,
                collection="gmw",
                properties={
                    "start_datetime": start_datetime,
                    "end_datetime": end_datetime,
                },
                id=out_cog_name,
                asset_name="mangrove",
                with_proj=True,
                with_raster=True,
            )

            # Write STAC doc
            stac_data = json.dumps(stac_doc.to_dict()).encode()
            await target_store.put_async(out_stac_name, stac_data)

            logging.info(f"Finished processing {out_cog_name}. STAC doc is at {out_stac_url}")


async def run_extract_gmw(
    source_location: str,
    source_zip_name: str,
    target_location: str,
    overwrite: bool,
    max_concurrent: int,
) -> None:
    """Async function to run the GMW extraction with parallel processing."""
    source_location = source_location.rstrip("/") # Remove trailing slash if present
    store = get_store_with_prefix_from_url(source_location)
    logging.info(f"Checking for source zip file at path {source_zip_name}...")
    source_exists = exists(store, source_zip_name)
    if not source_exists:
        logging.error(
            f"Source zip file does not exist at {source_location}. Cannot extract."
        )
        raise typer.Exit(code=1)
    else:
        logging.info(
            f"Source zip file found at {source_location}, proceeding with extraction."
        )

    # Ensure that target_location is absolute path if local, otherwise STAC item href will be relative which is broken.
    if not target_location.startswith("s3://") and not target_location.startswith("http"):
        # Make target location absolute path if local. Works for "./cache" and "file://cache" relative paths.
        target_location = str(Path(target_location).absolute())

    target_store = get_store_with_prefix_from_url(target_location)

    # Open the zip file, and extract all files into memory
    # Load the file as bytes first
    logging.info("Loading data into memory")
    zip_bytes = BytesIO(store.get(source_zip_name).bytes())
    logging.info("Finished loading data")

    # Create semaphore to limit concurrent operations
    semaphore = asyncio.Semaphore(max_concurrent)

    with ZipFile(zip_bytes) as z:
        # Get list of TIF files to process
        tif_files = [name for name in z.namelist() if name.endswith(".tif")]
        logging.info(
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

    logging.info("GMW extraction process completed.")


# This is run on a single source zip of many geotiffs.
# Unzips. For each geotiff, writes COG and STAC item. Uses Rasterio to read geotiff into memory as xarray. writes COG using ODC. Make json using rio-stac and write it.
@gmw_app.command("extract")
def extract_gmw(
    source_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to store the extracted GMW files.",
    ),
    # This is just for the V3 workflow where we extract many zip files. Each zip file has an extract command so we could get rid of the source_zip_name param.
    source_zip_name: str = typer.Option(
        ...,
        help="Name of the zip file to extract the GMW data from.",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to store the extracted GMW files.",
        # This must be an absolute path. Otherwise the STAC href attribute will be a relative path which breaks when used.
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing files during extraction."
    ),
    max_concurrent: int = typer.Option(
        32, help="Maximum number of files to process concurrently."
    ),
) -> None:
    logging.info("Starting GMW extraction process...")
    asyncio.run(
        run_extract_gmw(
            source_location, source_zip_name, target_location, overwrite, max_concurrent
        )
    )


async def run_index_gmw(
    source_location: str, target_location: str, overwrite: bool = True
) -> None:
    source_store = get_store_with_prefix_from_url(source_location)
    target_store = get_store_with_prefix_from_url(target_location)
    file_name = "gmw.parquet"
    target_url = f"{target_location}/{file_name}"

    # Check for existing geoparquet file
    if exists(target_store, file_name) and not overwrite:
        logging.info(
            f"Parquet file already exists at {target_url}, skipping indexing."
        )
        return
    else:
        if overwrite:
            logging.info("Overwrite is enabled, re-indexing GMW.")
        else:
            logging.info("Parquet file does not exist, proceeding with indexing.")

    # Find all the the GMW STAC files
    # Searches recursively. It needs to for v3 (and v4)
    item_dicts = await get_stac_item_dicts_from_store(source_store)


    logging.info(f"Writing {len(item_dicts)} STAC items to parquet at {target_url}")
    with suppress_rust_output():
        await write(file_name, item_dicts, store=target_store) # rustac infers that it is writing a parquet format from filename

    logging.info(f"Parquet write completed, wrote to {target_url}")


# Writes a parquet index of all the GMW STAC items found at the source location.
# Finds all STAC item jsons, reads them, writes a STAC-Geoparquet index file to the target location using rustac.
@gmw_app.command("index")
def index_gmw(
    source_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to the GMW files.",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (local or s3://) to store the indexed GMW parquet file.",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing index file"),
) -> None:
    logging.info("Starting GMW indexing process...")
    asyncio.run(run_index_gmw(source_location, target_location, overwrite))
    logging.info("GMW indexing process completed.")
