import asyncio
import logging
import re
from datetime import datetime
from io import BytesIO

import requests
import sedona.db
import typer
from obstore.store import ObjectStore

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
)

buildings_app = typer.Typer()


def _get_all_country_parquet_urls(source_location: str) -> list[str]:
    urls = []
    logging.info("Scraping source coop for all parquet files...")
    # TODO: Get country_codes dynamically from source_store scraping
    # TODO: fetch https://source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country HTML and get all <a> links that have title like "country_iso=AFG"
    # Example: <a title="country_iso=AFG" class="ObjectBrowser_item__v_6Zb" data-focused="true" href="/vida/google-microsoft-open-buildings/geoparquet/by_country/country_iso=AFG" style="display: flex; align-items: center; gap: var(--space-2); min-width: 0px; flex: 1 1 0%; opacity: 1; cursor: pointer;"><svg width="16" height="16" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6.1584 3.13508C6.35985 2.94621 6.67627 2.95642 6.86514 3.15788L10.6151 7.15788C10.7954 7.3502 10.7954 7.64949 10.6151 7.84182L6.86514 11.8418C6.67627 12.0433 6.35985 12.0535 6.1584 11.8646C5.95694 11.6757 5.94673 11.3593 6.1356 11.1579L9.565 7.49985L6.1356 3.84182C5.94673 3.64036 5.95694 3.32394 6.1584 3.13508Z" fill="currentColor" fill-rule="evenodd" clip-rule="evenodd"></path></svg><span class="rt-Text" style="font-family: var(--code-font-family); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0px; flex: 1 1 0%;">country_iso=AFG</span></a>
    # country_codes = ["AFG", "AGO", "ALB", "AND" , "ARE", "ARG", "ARM", "ATG", "AUS"] # TODO: Get all dynamically from source_store listing
    country_codes = ["AFG", "AUS"]
    for country_iso in country_codes:
        url = f"{source_location}country_iso={country_iso}/{country_iso}.parquet"
        urls.append(url)
    logging.info(f"Found {len(urls)} country files to cache from {source_location}.")
    return urls


async def _get_bounds_from_parquet(
        url: str,
        semaphore: asyncio.Semaphore,
        sd: sedona.db.context.SedonaContext
    ) -> tuple[float, float, float, float]:
    start_time = datetime.now()
    # # sd.read_parquet(url).to_view("buildings", overwrite=True)
    # # TODO: Remove timing logs later
    # # total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
    # # logging.info(f"Time taken to initialise: {total_seconds} seconds")
    # # Return only the CRS and bounding box of all geometries

    # # TODO: Try just reading the file metadata for bbox instead of loading all data into memory.
    # from pyarrow import dataset

    # ds = dataset.dataset(url)
    # ds_fragments = list(ds.get_fragments())
    # len(ds_fragments)

    # # https://sedona.apache.org/latest/tutorial/files/geoparquet-sedona-spark/

    # start_time = datetime.now()
    # # ST_SRID(geometry) as crs,

    # # This is very slow because it must calculate over all rows.
    # result = sd.sql("""
    #     SELECT
    #         ST_Envelope_Agg(geometry) as bbox
    #     FROM buildings
    # """).to_pandas()
    # # if len(geometry) == 0:
    # total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
    # logging.info(f"Time taken to find bbox: {total_seconds} seconds")

    # logging.info(f"Fetched bounds for {url}: {(result['xmin'][0], result['ymin'][0], result['xmax'][0], result['ymax'][0])}")
    # logging.info(f"crs: {result['crs'][0]}")

    # Load the parquet file from the URL and get the bounds
    # async with aiohttp.ClientSession() as session:
    #     async with session.get(url) as response:
    #         if response.status != 200:
    #             raise Exception(f"Failed to fetch parquet file from {url}, status code: {response.status}")
    #         data = await response.read()
    #         with BytesIO(data) as f:
    #             sd = sedona.db.connect()
    #             gdf = sd.read_parquet(f).to_pandas()
    #             total_bounds = gdf.total_bounds  # returns (minx, miny, maxx, maxy)
    #             return (total_bounds[0], total_bounds[1], total_bounds[2], total_bounds[3])

    # # Try this method for performance.
    # import pyarrow.parquet as pq

    # # Open the Parquet file
    # pf = pq.ParquetFile(url)

    # # Check for GeoParquet metadata
    # geo_meta = pf.metadata.metadata.get(b'geo')
    # if geo_meta:
    #     import json
    #     geo_info = json.loads(geo_meta.decode())
    #     # Usually, the geometry column is named 'geometry'
    #     bbox = geo_info['columns']['geometry']['bbox']
    #     print("Bounding box:", bbox)
    # else:
    #     print("No GeoParquet metadata found.")


    # import duckdb

    # # Connect to DuckDB (in-memory)
    # con = duckdb.connect()

    # # Query the Parquet metadata
    # meta = con.execute(f"SELECT * FROM parquet_metadata('{url}')").fetchdf()
    # bounds = con.execute(f"SELECT st_extent(ST_Extent_Agg(COLUMNS(geometry)))::BOX_2D FROM '{url}'").fetchdf()
    # print(meta)
    # print(meta.columns.tolist())

    # TODO: Get the bbox metadata with a library. Couldn't find a way to do it with SedonaDb, PyArrow, or DuckDB.
    # PyArrow needs a local or ObjectStorage file. The others don't expose the correct metadata from what I have found.


    async with semaphore:
        # Get file size
        head = requests.head(url)
        file_size = int(head.headers["Content-Length"])

        # Read last 64KB (footer and metadata)
        footer_size = 64 * 1024
        start = max(0, file_size - footer_size)
        headers = {"Range": f"bytes={start}-{file_size-1}"}
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()

        # Create a BytesIO object with the correct offset
        footer_bytes = resp.content

        # Decode as much as possible, ignoring errors
        text = footer_bytes.decode("utf-8", errors="ignore")

        # Find the first bbox array in the text (pattern: "bbox": [ ... ])
        match = re.search(r'"bbox"\s*:\s*\[([^\]]+)\]', text)
        if match:
            bbox_str = match.group(1)
            # Convert the string to a tuple of floats
            bbox = tuple(float(x.strip()) for x in bbox_str.split(","))
            print("Bounding box:", bbox)

            # TODO: Remove timing logs later
            total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
            logging.info(f"Time taken to get bbox: {total_seconds} seconds")
            return bbox
        else:
            raise ValueError("No bbox found in footer bytes.")


async def _run_index_buildings(
        countries_to_cache: list[str],
        target_store: ObjectStore,
        target_file_name: str,
        max_concurrent: int = 16,
) -> None:
    sd = sedona.db.connect()
    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _get_bounds_from_parquet(
            url=url,
            semaphore=semaphore,
            sd=sd
        )
        for url in countries_to_cache
    ]
    results = await asyncio.gather(*tasks)
    logging.info("Bboxes collected for all country parquet files.")
    for url, bbox in zip(countries_to_cache, results):
        print(f"{url}: {bbox}")

    import pandas as pd
    output = pd.DataFrame([
        {"url": url, "bbox": bbox}
        for url, bbox in zip(countries_to_cache, results)
    ])

    # TODO: Check if we can search to get bbox without having to download or load all data into memory. This would make cache step redundant and improve performance greatly.

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
    max_concurrent: int = typer.Option(16, help="Maximum number of files to process at once."),
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
    asyncio.run(_run_index_buildings(country_urls, max_concurrent=max_concurrent, target_store=target_store, target_file_name=target_file_name))
    logging.info("Index buildings dataset process completed.")

if __name__ == "__main__":
    buildings_app()
