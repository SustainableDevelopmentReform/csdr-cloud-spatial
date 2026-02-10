import asyncio
import logging
import os
from io import BytesIO
from zipfile import ZipFile

import geopandas as gpd
import numpy as np
import pandas as pd
import typer
from obstore.store import ObjectStore

from csdr.io import (
    exists,
    find_matching_files,
    get_store_with_prefix_from_url,
    write_gdf_to_parquet,
)
from csdr.utils import CSDRException

dataset_zip_shp_app = typer.Typer()


async def _unzip_single_zip(
    zip_path_and_file_name: str,
    source_store: ObjectStore,
    target_store: ObjectStore,
    overwrite: bool,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        source_file_name = os.path.basename(
            zip_path_and_file_name
        )  # Remove nested path folders (if any). Keep only zip file name and extension
        path_without_extension = os.path.splitext(source_file_name)[
            0
        ]  # Remove .zip extension
        if (
            exists(
                target_store, f"{path_without_extension}/Reef-Extent/reefextent.gpkg"
            )
            and not overwrite
        ):
            logging.info(
                f"Skipping {zip_path_and_file_name}; output exists at target store and overwrite is off."
            )
            return
        logging.info(
            f"Unzipping {zip_path_and_file_name} to target store (overwrite={'on' if overwrite else 'off'}) ..."
        )
        zip_bytes = BytesIO(source_store.get(zip_path_and_file_name).bytes())
        with ZipFile(zip_bytes) as zip_item:
            for (
                zip_source_file_name
            ) in zip_item.namelist():  # Iterate through each file in the zip
                if zip_source_file_name.endswith("/"):  # Skip directories
                    continue
                data = zip_item.read(zip_source_file_name)
                target_path = f"{path_without_extension}/{zip_source_file_name}"  # Add the original zip file name (without .zip) as a folder prefix
                # TODO: Is this exists check needed? It is a bit redundant but could be a good safeguard in case parial unzips occured.
                if exists(target_store, target_path) and not overwrite:
                    logging.info(
                        f"Skipping file {target_path}, already exists and overwrite is off."
                    )
                    continue
                target_store.put(target_path, data)
        logging.info(f"Finished unzipping {zip_path_and_file_name} to target store.")


async def _run_extract_aca(
    source_location: str,
    target_location: str,
    overwrite: bool,
    max_concurrent: int,
) -> None:
    logging.info("Finding zip files to extract...")
    # TODO: We could make this much more efficient by somehow just getting the reefextent.gpkg because the whole of Northern Caribbean data is 11.84 GB (unzipped) but the reefextent.gpkg is only 38 MB.
    source_store = get_store_with_prefix_from_url(
        source_location, client_options={"timeout": "2 hours"}
    )  # timeout is increased because we need to download large files
    target_store = get_store_with_prefix_from_url(target_location)
    all_zip_file_paths = find_matching_files(
        source_store, r"\.zip$"
    )  # Match all .zip files
    logging.info(
        f"Found {len(all_zip_file_paths)} zip files to extract from {source_location}. Unzipping..."
    )

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _unzip_single_zip(
            zip_path_and_file_name=zip_file_path,
            source_store=source_store,
            target_store=target_store,
            overwrite=overwrite,
            semaphore=semaphore,
        )
        for zip_file_path in all_zip_file_paths
    ]
    await asyncio.gather(*tasks)
    logging.info("Completed unzipping all region zips.")


# ACA Extract gets all zip files from source_location, unzips them to target_location, preserving folder structure.
@dataset_zip_shp_app.command("extract")
def extract_aca(
    source_location: str = typer.Option(
        ...,
        help="S3 or local path containing zip files to extract (e.g. s3://bucket/datasets/aca/0-0-1/raw)",
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write unzipped files (e.g. s3://bucket/datasets/aca/0-0-1/data)",
    ),
    overwrite: bool = typer.Option(
        True, help="Overwrite files if they exist at target."
    ),
    max_concurrent: int = typer.Option(
        16, help="Maximum number of unzips to process at once."
    ),
) -> None:
    logging.info("Starting ACA extraction process ...")
    source_location = source_location.rstrip("/")
    target_location = target_location.rstrip("/")
    asyncio.run(
        _run_extract_aca(source_location, target_location, overwrite, max_concurrent)
    )
    logging.info("ACA extraction process completed.")


# _partition_parquet partitions a large GeoParquet file into smaller Parquet files based on a global grid.
# _partition_parquet could be moved to utils if needed for other datasets too.
def _partition_parquet(
    target_store: ObjectStore,
    gdf: gpd.GeoDataFrame,
    grid_chunks: int = 10,
    overwrite: bool = True,
) -> None:
    # Default size is 20 x 10 grid cells of 18 x 18 degrees. Large countries will be spread over many partitions, medium and small countries hopefully fit into 1 or 2.
    # Number of edges is one more than number of intervals
    grid_size_lon = (
        grid_chunks * 2 + 1
    )  # Default is 20 longitude cells each 360 / 20 = 18 degrees wide
    grid_size_lat = (
        grid_chunks + 1
    )  # Default is 10 latitude cells each 180 / 10 = 18 degrees high

    # Define grid edges
    lon_edges = np.linspace(-180, 180, grid_size_lon)
    lat_edges = np.linspace(-90, 90, grid_size_lat)
    # Get centroid coordinates
    gdf["lon"] = gdf.geometry.centroid.x
    gdf["lat"] = gdf.geometry.centroid.y

    # Assign grid cell indices
    gdf["lon_bin"] = pd.cut(gdf["lon"], lon_edges, labels=False, include_lowest=True)
    gdf["lat_bin"] = pd.cut(gdf["lat"], lat_edges, labels=False, include_lowest=True)

    # Create a partition label
    gdf["partition"] = gdf["lon_bin"].astype(str) + "_" + gdf["lat_bin"].astype(str)

    # Delete all files in partition/ folder first if overwrite is on. Otherwise if partition settings are changed, reading the data will be broken due to duplication in different partition structures.
    if overwrite:
        logging.info("Overwrite is on, deleting existing partition files ...")
        partition_files = find_matching_files(
            target_store, prefix="partition/", pattern=r"\.parquet$"
        )
        print(f"Deleting {len(partition_files)} partition files...")
        for file in partition_files:
            print(f"Deleting file {file}...")
            target_store.delete(f"{file}")
        logging.info("Deleted all partitioned Parquet files ...")
    else:
        logging.info("Overwrite is off, existing partition files will be preserved ...")

    # Write each partition to a separate Parquet file
    for partition, group in gdf.groupby("partition"):
        file_name = f"partition/reefextent_{partition}.parquet"
        file_exists = exists(target_store, file_name)
        if (file_exists and overwrite) or not file_exists:
            # Remove columns, then write partitioned file
            gdf_partition = group.drop(
                ["lon", "lat", "lon_bin", "lat_bin", "partition"], axis=1
            )
            write_gdf_to_parquet(gdf_partition, target_store, file_name)
        else:
            logging.info(
                f"Skipping partition {partition}, already exists and overwrite is off."
            )


async def _run_index_aca(
    source_location: str,
    target_location: str,
    overwrite: bool,
) -> None:
    target_store = get_store_with_prefix_from_url(target_location)
    target_file_name = "reefextent.parquet"
    if exists(target_store, target_file_name) and not overwrite:
        logging.info(
            f"Skipping index: {target_file_name} already exists and overwrite is off."
        )
        return
    source_store = get_store_with_prefix_from_url(source_location)
    logging.info(f"Searching for all reefextent.gpkg under {source_location} ...")
    gpkg_paths = find_matching_files(source_store, "reefextent.gpkg")
    logging.info(
        f"Found {len(gpkg_paths)} reefextent.gpkg files to merge into {target_file_name}"
    )
    dfs = []
    for path in gpkg_paths:
        logging.info(f"Reading {path} ...")
        # Get a file-like object from obstore, read it into a GeoDataFrame, append gdf to list.
        bytes_obj = source_store.get(path).bytes()
        # TODO: Use io.read_geospatial_file
        gdf = gpd.read_file(BytesIO(bytes_obj))
        dfs.append(gdf)
    if not dfs:
        raise CSDRException("No GPKG files found, nothing to merge.")
    merged_gdf = gpd.GeoDataFrame(pd.concat(dfs, ignore_index=True))
    logging.info(f"Writing merged GeoParquet to {target_file_name}")
    with BytesIO() as f:
        merged_gdf.to_parquet(f, index=False)
        f.seek(0)
        target_store.put(target_file_name, f.read())
    logging.info("Merge and export to GeoParquet completed.")
    logging.info("Starting partitioning of the merged GeoParquet ...")
    _partition_parquet(target_store, merged_gdf, grid_size=10, overwrite=overwrite)
    logging.info("Partitioning completed.")


# ACA Index gets all of the nested reefextent.gpkg files (one per region folder), and merges them into a single reefextent.parquet file at target_location.
@dataset_zip_shp_app.command("index")
def index_aca(
    source_location: str = typer.Option(
        ...,
        help="S3 or local path to extracted ACA files (e.g. s3://bucket/datasets/aca/0-0-1/data)",
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write reefextent.parquet index (e.g. s3://bucket/datasets/aca/0-0-1/)",
    ),
    overwrite: bool = typer.Option(True, help="Overwrite output file if it exists."),
) -> None:
    logging.info("Starting ACA merge/index process ...")
    asyncio.run(_run_index_aca(source_location, target_location, overwrite))
    logging.info("ACA merge/index process completed.")


if __name__ == "__main__":
    dataset_zip_shp_app()
