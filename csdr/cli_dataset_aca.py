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
    Finds files in the store with a given glob pattern (recursively for local, non-s3).
    S3Store .ls() returns file paths under prefix. 
    """
    try:
        keys = store.ls(prefix)
        matches = [k for k in keys if fnmatch.fnmatch(os.path.basename(k), pattern)]
    except Exception:
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
        output_dir = f"{dest_prefix}/{unzip_dir}".rstrip("/")
        out_check_path = f"{output_dir}/reefextent/reefextent.gpkg"
        if exists(dest, out_check_path) and not overwrite:
            logger.info(f"Skipping {zip_path}; output exists at {out_check_path} and overwrite is off.")
            return
        logger.info(f"Unzipping {zip_path} to {output_dir} (overwrite={'on' if overwrite else 'off'}) ...")
        zip_bytes = BytesIO(store.get(zip_path).bytes())
        with ZipFile(zip_bytes) as z:
            for member in z.namelist():
                if member.endswith("/"):
                    continue
                data = z.read(member)
                target_path = f"{output_dir}/{member}"
                if exists(dest, target_path) and not overwrite:
                    logger.info(f"Skipping file {target_path}, already exists and overwrite is off.")
                    continue
                dest.put(target_path, data)
        logger.info(f"Finished unzipping {zip_path} to {output_dir}")

async def run_extract_aca(
    source_location: str,
    target_location: str,
    overwrite: bool,
    max_concurrent: int,
):
    store = get_store_for_url(source_location)
    dest = get_store_for_url(target_location)
    src_prefix = get_prefix(source_location)
    dst_prefix = get_prefix(target_location)
    all_zips = find_matching_files(store, src_prefix, "*.zip")
    logger.info(f"Found {len(all_zips)} zip files to extract from {src_prefix}")
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
    await asyncio.gather(*tasks)
    logger.info("Completed unzipping all region zips.")

@aca_app.command("extract")
def extract_aca(
    source_location: str = typer.Option(
        ...,
        help="S3/local path containing zip files to extract (e.g. s3://bucket/datasets/aca/0-0-1/cache/)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3/local path to write unzipped files (e.g. s3://bucket/datasets/aca/0-0-1/data/)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite files if they exist at target."),
    max_concurrent: int = typer.Option(16, help="Maximum number of unzips to process at once."),
):
    logger.info("Starting ACA extraction process ...")
    asyncio.run(run_extract_aca(source_location, target_location, overwrite, max_concurrent))
    logger.info("ACA extraction process completed.")

async def run_index_aca(
    source_location: str,
    target_location: str,
    overwrite: bool,
):
    dest = get_store_for_url(target_location)
    # Outfile should be the path "prefix/aca.parquet"
    dest_prefix = get_prefix(target_location)
    out_basename = "aca.parquet"
    out_path = f"{dest_prefix}/{out_basename}" if dest_prefix else out_basename
    if exists(dest, out_path) and not overwrite:
        logger.info(f"Skipping index: {out_path} already exists and overwrite is off.")
        return
    src_store = get_store_for_url(source_location)
    src_prefix = get_prefix(source_location)
    logger.info(f"Searching for all reefextent.gpkg under {src_prefix} ...")
    gpkg_paths = find_matching_files(src_store, src_prefix, "reefextent.gpkg")
    gpkg_paths = [p for p in gpkg_paths if "reefextent/reefextent.gpkg" in p]
    logger.info(f"Found {len(gpkg_paths)} reefextent.gpkg files to merge into {out_path}")
    dfs = []
    for path in gpkg_paths:
        logger.info(f"Reading {path} ...")
        with src_store.open(path, "rb") as f:
            dfs.append(gpd.read_file(f))
    if not dfs:
        logger.warning("No GPKG files found, nothing to merge.")
        return
    merged_gdf = gpd.GeoDataFrame(pd.concat(dfs, ignore_index=True))
    logger.info(f"Writing merged GeoParquet to {out_path}")
    with BytesIO() as f:
        merged_gdf.to_parquet(f, index=False)
        f.seek(0)
        dest.put(out_path, f.read())
    logger.info("Merge and export to GeoParquet completed.")

@aca_app.command("index")
def index_aca(
    source_location: str = typer.Option(
        ...,
        help="S3/local path to extracted ACA files (e.g. s3://bucket/datasets/aca/0-0-1/data/)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3/local path to write aca.parquet index (e.g. s3://bucket/datasets/aca/0-0-1/)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite output file if it exists."),
):
    logger.info("Starting ACA merge/index process ...")
    asyncio.run(run_index_aca(source_location, target_location, overwrite))
    logger.info("ACA merge/index process completed.")

if __name__ == "__main__":
    aca_app()
