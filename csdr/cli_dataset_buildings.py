import asyncio
import logging
import re
from io import BytesIO

import boto3
import geopandas as gpd
import pandas as pd
import requests
import typer
from botocore import UNSIGNED
from botocore.client import Config
from obstore.store import ObjectStore
from shapely.geometry import box

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
)

buildings_app = typer.Typer()


def _get_all_country_parquet_urls(source_location: str) -> list[str]:
    logging.info(f"Scraping country parquet URLs from {source_location} ...")
    code_url_pairs = set()
    s3 = boto3.client(
        "s3",
        endpoint_url="https://data.source.coop",
        config=Config(signature_version=UNSIGNED),
    )
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket="vida", Prefix="google-microsoft-open-buildings/"):
        for obj in page.get("Contents", []):
            match = re.search(r"country_iso=([A-Z]{3})/", obj["Key"])
            if match:
                code = match.group(1)
                url = f"{source_location}country_iso={code}/{code}.parquet"
                code_url_pairs.add((code, url))

    if not code_url_pairs:
        raise RuntimeError("No country files found in S3 listing via boto3.")

    code_url_pairs = sorted(code_url_pairs)
    logging.info(f"Found {len(code_url_pairs)} country files to cache from {source_location}.")
    return code_url_pairs


async def _get_bounds_from_parquet(
        url: str,
        code: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[float, float, float, float]:

    # Here we get large parquet files' bbox from metadata. This means that we don't have to download/load all data into memory.
    # It takes about 2-3 seconds per file this way, rather than minutes to download/load entire file.

    # TODO: Get the bbox metadata with a library instead of DIY. Couldn't find a way to do it with SedonaDb, PyArrow, or DuckDB.
    # PyArrow needs a local or ObjectStorage file. The other libraries don't expose the correct metadata from what I have found.

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
            logging.info(f"Bounding box {bbox} for {code}")

            return bbox
        else:
            raise ValueError("No bbox found in footer bytes.")


async def _run_index_buildings(
        countries_to_cache: list[str],
        target_store: ObjectStore,
        target_file_name: str,
        max_concurrent: int,
) -> None:
    semaphore = asyncio.Semaphore(max_concurrent)
    # Launch all bbox fetches concurrently
    tasks = [
        _get_bounds_from_parquet(url=url, code=code, semaphore=semaphore)
        for code, url in countries_to_cache
    ]
    bound_results = await asyncio.gather(*tasks)
    logging.info("Bboxes collected for all country parquet files.")

    output = pd.DataFrame([
        {"code": code, "url": url, "bbox": bbox}
        for (code, url), bbox in zip(countries_to_cache, bound_results)
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
        "https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country/",
        help="HTTP url containing parquet files to cache (e.g. https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country/)"
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

    # Make sure source location ends with "/"
    if not source_location.endswith("/"):
        source_location += "/"

    country_urls = _get_all_country_parquet_urls(source_location)
    asyncio.run(_run_index_buildings(country_urls, target_store, target_file_name, max_concurrent))
    logging.info("Index buildings dataset process completed.")

if __name__ == "__main__":
    buildings_app()
