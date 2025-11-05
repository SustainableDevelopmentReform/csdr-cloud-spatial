import asyncio
import os
import fnmatch
from io import BytesIO
from zipfile import ZipFile

import typer
import geopandas as gpd
import pandas as pd
from loguru import logger

from csdr.io import (
    exists,
    get_prefix,
    get_store_for_url,
)

aca_app = typer.Typer()

def find_matching_files(store, prefix, pattern):
    """
    Recursively finds files in the store beneath prefix that match the glob pattern.
    For S3Store and similar, expects .ls() returns list of file paths as strings.
    """
    try:
        keys = store.ls(prefix)
        matches = [k for k in keys if fnmatch.fnmatch(os.path.basename(k), pattern)]
        # Optionally recursively look for more, but typically .ls() shows all under the prefix.
    except Exception:
        # For LocalStore or local files
        matches = []
        for root, dirs, files in os.walk(prefix):
            for fname in files:
                if fnmatch.fnmatch(fname, pattern):
                    matches.append(os.path.join(root, fname))
    return matches

async def unzip_single_zip(
    zip_path: str,
    store,
    dest,
    dest_prefix: str,
    overwrite: bool,
    semaphore: asyncio.Semaphore,
):
    async with semaphore:
        filename = os.path.basename(zip_path)
        unzip_dir = os.path.splitext(filename)[0]
        output_dir = f"{dest_prefix}/{unzip_dir}"
        out_check_path = f"{output_dir}/reefextent/reefextent.gpkg"
        if exists(dest, out_check_path) and not overwrite:
            logger.info(f"Unzipped output exists at {out_check_path} and overwrite is off, skipping.")
            return
        logger.info(f"Either unzipped output does not exist or overwrite is on. Unzipping {zip_path} to {output_dir}...") # improve this to specify which boolean is driving this logic
        zip_bytes = BytesIO(store.get(zip_path).bytes())
        with ZipFile(zip_bytes) as z:
            for member in z.namelist():
                data = z.read(member)
                target_path = f"{output_dir}/{member}"
                if member.endswith("/"):
                    continue
                if exists(dest, target_path) and not overwrite:
                    logger.info(f"File {target_path} exists, skipping.")
                    continue
                dest.put(target_path, data)
        logger.info(f"Finished unzipping {zip_path}")

@aca_app.command("unzip")
def unzip_aca(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to search for zip files to unzip.",
        default="s3://csdr-public-dev/datasets/aca/0-0-1/cache/",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the extracted ACA files.",
        default="s3://csdr-public-dev/datasets/aca/0-0-1/cache/unzip/",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing files during extraction."),
    max_concurrent: int = typer.Option(16, help="Maximum number of unzips to process concurrently."),
):
    logger.info("Listing zip files for extraction...")
    store = get_store_for_url(source_location)
    dest = get_store_for_url(target_location)
    src_prefix = get_prefix(source_location)
    dst_prefix = get_prefix(target_location)
    all_zips = find_matching_files(store, src_prefix, "*.zip")
    logger.info(f"Found {len(all_zips)} zip files to extract.")
    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        unzip_single_zip(
            zip_path=z,
            store=store,
            dest=dest,
            dest_prefix=dst_prefix,
            overwrite=overwrite,
            semaphore=semaphore,
        )
        for z in all_zips
    ]
    asyncio.run(asyncio.gather(*tasks))
    logger.info("Completed unzipping all region zips.")

@aca_app.command("merge")
def merge_aca(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to unzipped ACA files.",
        default="s3://csdr-public-dev/datasets/aca/0-0-1/cache/unzip/",
    ),
    target_location: str = typer.Option(
        help="Output path (file:// or s3://) to store the merged geoparquet.",
        default="s3://csdr-public-dev/datasets/aca/0-0-1/aca.parquet",
    ),
    overwrite: bool = typer.Option(True, help="Replace existing output parquet if present."),
):
    logger.info("Starting ACA region GPKG merge...")
    dest = get_store_for_url(target_location)
    out_gpqk = get_prefix(target_location) or os.path.basename(target_location)
    if exists(dest, out_gpqk) and not overwrite:
        logger.info(f"Merged output {target_location} exists, skipping merge.")
        return
    logger.info(f"Either merged output does not exist or overwrite is on. Preparing to merge.") # improve this to specify which boolean is driving this logic
    src_store = get_store_for_url(source_location)
    src_prefix = get_prefix(source_location)
    logger.info(f"Searching for all reefextent.gpkg under {source_location} ...")
    gpkg_paths = find_matching_files(src_store, src_prefix, "reefextent.gpkg")
    gpkg_paths = [p for p in gpkg_paths if "reefextent/reefextent.gpkg" in p]
    logger.info(f"Found {len(gpkg_paths)} reefextent.gpkg files to merge.")
    dfs = []
    for path in gpkg_paths:
        logger.info(f"Reading {path} ...")
        with src_store.open(path, "rb") as f:
            dfs.append(gpd.read_file(f))
    logger.info("Read all GPKGs, now merging...")
    if not dfs:
        logger.warning("No GPKG files found to merge. Exiting.")
        return
    merged_gdf = gpd.GeoDataFrame(pd.concat(dfs, ignore_index=True))
    logger.info(f"Writing merged geoparquet to {target_location}")
    with BytesIO() as f:
        merged_gdf.to_parquet(f, index=False)
        f.seek(0)
        dest.put(out_gpqk, f.read())
    logger.info("Merge and export to geoparquet completed.")

if __name__ == "__main__":
    aca_app()
