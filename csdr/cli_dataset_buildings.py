import asyncio
import logging
import re
from io import BytesIO

import geopandas as gpd
import pandas as pd
import requests
import typer
from obstore.store import ObjectStore
from shapely.geometry import box

from csdr.io import (
    exists,
    find_matching_files,
    get_store_with_prefix_from_url,
    split_path_and_file_name_from_url,
)

buildings_app = typer.Typer()


class NoCountryParquetFilesFound(Exception):
    pass


def _get_all_country_parquet_urls(source_location: str) -> list[str]:
    logging.info(f"Scraping country parquet URLs from {source_location} ...")
    # List objects via Source Coop S3 proxy.
    store = get_store_with_prefix_from_url(
        "s3://vida/google-microsoft-open-buildings/geoparquet/by_country/",
        region="us-east-1",
        skip_signature=True,
        endpoint_url="https://data.source.coop"
    )

    pattern = r'^(?!country_iso=None/None\.parquet$).+\.parquet$' # All parquet files are needed except None/None.parquet
    parquet_files = find_matching_files(store, pattern=pattern)

    number_found = len(parquet_files)
    logging.info(f"Number of parquet files found: {number_found}")

    if number_found == 0 or parquet_files is None:
        raise NoCountryParquetFilesFound("No country parquet files found in Source Coop S3.")
    
    parquet_urls = [f"{source_location}/{file}.parquet" for file in parquet_files]

    return parquet_urls

async def _get_bounds_from_parquet(
        url: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[float, float, float, float]:

    # Here we get large parquet files' bbox from metadata. This means that we don't have to download/load all data into memory.
    # It takes about 2-3 seconds per file this way, rather than minutes to download/load entire file.

    # TODO: Get the bbox metadata with a library instead of DIY. Couldn't find a way to do it with SedonaDb, PyArrow, or DuckDB.
    # PyArrow needs a local or ObjectStorage file. The other libraries don't expose the correct metadata from what I have found.

    logging.info(f"Fetching bounding box from parquet file at {url} ...")
    async with semaphore:
        head = requests.head(url)
        file_size = int(head.headers["Content-Length"])

        # Read last 64KB (footer and metadata)
        footer_size = 64 * 1024
        start = max(0, file_size - footer_size)
        headers = {"Range": f"bytes={start}-{file_size-1}"}
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()

        footer_bytes = resp.content
        text = footer_bytes.decode("utf-8", errors="ignore")

        # Find the first bbox array in the text
        match = re.search(r'"bbox"\s*:\s*\[([^\]]+)\]', text)
        if match:
            bbox_str = match.group(1)
            bbox = tuple(float(x.strip()) for x in bbox_str.split(","))
            logging.info(f"Bounding box {bbox} for {url}")

            return bbox
        else:
            raise ValueError("No bbox found in footer bytes.")


async def _run_index_buildings(
        parquet_urls: list[str],
        target_store: ObjectStore,
        target_file_name: str,
        max_concurrent: int,
) -> None:
    semaphore = asyncio.Semaphore(max_concurrent)
    # Launch all bbox fetches concurrently
    tasks = [
        _get_bounds_from_parquet(url, semaphore=semaphore)
        for url in parquet_urls
    ]
    bound_results = await asyncio.gather(*tasks)
    logging.info("Bboxes collected for all country parquet files.")

    output = pd.DataFrame([
        {"code": split_path_and_file_name_from_url(url)[1], "url": url, "bbox": bbox}
        for url, bbox in zip(parquet_urls, bound_results)
    ])

    # Write bounds as separate columns (minx, miny, maxx, maxy) and a geometry column.
    output[['minx', 'miny', 'maxx', 'maxy']] = pd.DataFrame(output['bbox'].tolist(), index=output.index)
    # Add bbox_wkt as a POLYGON geometry column (WKT string)
    output['bbox_wkt'] = output.apply(
        lambda row: f"POLYGON(({row['minx']} {row['miny']}, {row['maxx']} {row['miny']}, {row['maxx']} {row['maxy']}, {row['minx']} {row['maxy']}, {row['minx']} {row['miny']}))",
        axis=1
    )
    output = output.drop(columns=['bbox'])
    # Add a native geometry column using shapely and geopandas
    output['geometry'] = output.apply(lambda row: box(row['minx'], row['miny'], row['maxx'], row['maxy']), axis=1)
    output = gpd.GeoDataFrame(output, geometry='geometry', crs='EPSG:4326')

    target_file_name = "buildings.parquet"
    logging.info(f"Writing index buildings parquet to {target_file_name}")
    with BytesIO() as f:
        output.to_parquet(f, index=False)
        f.seek(0)
        target_store.put(target_file_name, f.read())
    logging.info("Index buildings dataset completed.")


# Buildings Index gets all of the parquet files (one per country) from source coop, and writes their name, path, and bounds to a single buildings.parquet file at target_location.
@buildings_app.command("index")
def index_buildings(
    source_location: str = typer.Option(
        "https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country",
        help="HTTP url containing parquet files to cache (e.g. https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country)"
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

    source_location = source_location.rstrip("/")

    parquet_urls = _get_all_country_parquet_urls(source_location)
    asyncio.run(_run_index_buildings(parquet_urls, target_store, target_file_name, max_concurrent))
    logging.info("Index buildings dataset process completed.")

if __name__ == "__main__":
    buildings_app()
