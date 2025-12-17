import asyncio
import json
import logging

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import typer
from obstore.fsspec import FsspecStore
from obstore.store import ObjectStore
from shapely.geometry import box

from csdr.io import (
    exists,
    find_matching_files,
    get_store_with_prefix_from_url,
    split_path_and_file_name_from_url,
    write_gdf_to_parquet,
)
from csdr.utils import CSDRException

buildings_app = typer.Typer()

def _get_all_country_parquet_urls(source_location_s3: str, source_proxy: str) -> pd.DataFrame:
    logging.info(f"Scraping country parquet URLs from {source_location_s3} ...")
    # List objects via Source Coop S3 proxy.
    store = get_store_with_prefix_from_url(
        source_location_s3,
        region="us-east-1",
        skip_signature=True,
        endpoint_url=source_proxy
    )

    pattern = r'^(?!country_iso=None/None\.parquet$).+\.parquet$' # All parquet files are needed except None/None.parquet
    parquet_files = find_matching_files(store, pattern=pattern)

    number_found = len(parquet_files)
    logging.info(f"Number of parquet files found: {number_found}")

    if number_found == 0 or parquet_files is None:
        raise CSDRException("No country parquet files found in Source Coop S3.")
    
    parquet_data = pd.DataFrame({
        "code": split_path_and_file_name_from_url(file)[1].replace(".parquet", ""),
        "url": f"{source_proxy}{source_location_s3.replace('s3://', '')}{file}",
        "s3_url": f"{source_location_s3}{file}",
        "s3_path": file,
    } for file in parquet_files)

    return parquet_data

async def _get_bounds_from_parquet(
        source_proxy: str,
        s3_url: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[float, float, float, float]:

    # Here we get large parquet files' bbox from metadata. This means that we don't have to download/load all data into memory.
    # It takes a few seconds per file this way, rather than minutes to download/load entire file.

    logging.info(f"Fetching bounding box from parquet file at {s3_url} ...")
    async with semaphore:
        fs = FsspecStore(
            "s3",
            region="us-east-1",
            skip_signature=True,
            endpoint_url=source_proxy
        )
        parquet_file = pq.ParquetFile(s3_url, filesystem=fs)

        meta = parquet_file.schema_arrow.metadata

        geo_json = meta[b'geo'].decode('utf-8')
        geo = json.loads(geo_json)

        bbox = geo["columns"]["geometry"]["bbox"]
        
        logging.info(f"Bounding box {bbox} for {s3_url}")

        return bbox


async def _run_index_buildings(
        source_proxy: str,
        parquet_data: pd.DataFrame,
        target_store: ObjectStore,
        target_file_name: str,
        max_concurrent: int,
) -> None:
    semaphore = asyncio.Semaphore(max_concurrent)
    # Launch all bbox fetches concurrently
    tasks = [
        _get_bounds_from_parquet(source_proxy, s3_url, semaphore=semaphore)
        for s3_url in parquet_data["s3_url"]
    ]
    bound_results = await asyncio.gather(*tasks)
    logging.info("Bboxes collected for all country parquet files.")

    parquet_data["bbox"] = bound_results # Append bbox to (code, url, s3_url) dataframe
    # Add geo data to df
    parquet_data[['minx', 'miny', 'maxx', 'maxy']] = pd.DataFrame(parquet_data['bbox'].tolist(), index=parquet_data.index)
    parquet_data['bbox_wkt'] = parquet_data.apply(
        lambda row: f"POLYGON(({row['minx']} {row['miny']}, {row['maxx']} {row['miny']}, {row['maxx']} {row['maxy']}, {row['minx']} {row['maxy']}, {row['minx']} {row['miny']}))",
        axis=1
    )
    parquet_data = parquet_data.drop(columns=['bbox'])
    parquet_data['geometry'] = parquet_data.apply(lambda row: box(row['minx'], row['miny'], row['maxx'], row['maxy']), axis=1)
    parquet_data = gpd.GeoDataFrame(parquet_data, geometry='geometry', crs='EPSG:4326')

    target_file_name = "buildings.parquet"
    logging.info(f"Writing index buildings parquet to {target_file_name}")
    write_gdf_to_parquet(parquet_data, target_store, target_file_name)
    logging.info("Index buildings dataset completed.")


# Buildings Index gets all of the parquet files (one per country) from source coop, and writes their name, path, and bounds to a single buildings.parquet file at target_location.
@buildings_app.command("index")
def index_buildings(
    source_location_s3: str = typer.Option(
        "s3://vida/google-microsoft-open-buildings/geoparquet/by_country/",
        help="S3 url containing parquet files to cache (e.g. s3://vida/google-microsoft-open-buildings/geoparquet/by_country/)"
    ),
    source_proxy: str = typer.Option(
        "https://data.source.coop/",
        help="HTTP url containing parquet files to cache (e.g. https://data.source.coop/)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write buildings.parquet index (e.g. s3://bucket/datasets/buildings/0-0-1/)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite output file if it exists."),
    max_concurrent: int = typer.Option(32, help="Maximum number of files to process at once."),
) -> None:
    logging.info("Starting buildings index process ...")
    
    target_store = get_store_with_prefix_from_url(target_location)
    target_file_name = "buildings.parquet"
    if exists(target_store, target_file_name) and not overwrite:
        logging.info(f"Skipping index: {target_file_name} already exists and overwrite is off.")
        return
    logging.info("Either file does not exist or overwrite is on, proceeding with indexing.")

    parquet_data = _get_all_country_parquet_urls(source_location_s3, source_proxy)
    asyncio.run(_run_index_buildings(source_proxy, parquet_data, target_store, target_file_name, max_concurrent))
    logging.info("Index buildings dataset process completed.")

if __name__ == "__main__":
    buildings_app()
