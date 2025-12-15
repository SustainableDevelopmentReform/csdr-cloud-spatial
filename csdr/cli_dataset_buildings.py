# This is nice. We may want to do a kind of "index in place" process for this, though. Depends how large the data is.
# I'd like to consider source.coop as a "canonical" data source, and read from there. (I'd also like us to be writing to there, which is a nice future goal.)
# https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial/pull/85#discussion_r2583388386

import asyncio
import logging
from io import BytesIO

import aiohttp
import sedona.db
import typer
from obstore.store import ObjectStore

from csdr.io import (
    exists,
    find_matching_files,
    get_store_with_prefix_from_url,
)

buildings_app = typer.Typer()


async def _download_parquet_file(source_location: str, country_iso: str, target_store: ObjectStore, overwrite: bool, semaphore: asyncio.Semaphore) -> None:
    # semaphore limits number of concurrent downloads
    async with semaphore:
        # Get file and write to target_store
        source_file_name = f"country_iso={country_iso}/{country_iso}.parquet"
        target_file_name = f"{country_iso}.parquet"
        if exists(target_store, target_file_name):
            if not overwrite:
                logging.info(f"Skipping {target_file_name}; output exists at target store and overwrite is off.")
                return
            else:
                logging.info(f"Overwrite is on. Re-downloading {source_file_name} ...")
        url = f"{source_location.rstrip('/')}/{source_file_name}"
        logging.info(f"Fetching {url} ...")
        timeout = aiohttp.ClientTimeout(total=3000) # 3000 seconds (50 minutes)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logging.error(f"Failed to fetch {url}: HTTP {resp.status}")
                    return
                data = await resp.read()
        logging.info(f"Writing {target_file_name} to target store ...")
        await target_store.put_async(target_file_name, data)


async def _run_cache_buildings(
    source_location: str,
    target_location: str,
    overwrite: bool,
    max_concurrent: int,
) -> None:
    logging.info("Scraping source coop for all parquet files...")
    target_store = get_store_with_prefix_from_url(target_location)

    countries_to_cache = []
    # TODO: fetch https://source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country HTML and get all <a> links that have title like "country_iso=AFG"
    # Example: <a title="country_iso=AFG" class="ObjectBrowser_item__v_6Zb" data-focused="true" href="/vida/google-microsoft-open-buildings/geoparquet/by_country/country_iso=AFG" style="display: flex; align-items: center; gap: var(--space-2); min-width: 0px; flex: 1 1 0%; opacity: 1; cursor: pointer;"><svg width="16" height="16" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M6.1584 3.13508C6.35985 2.94621 6.67627 2.95642 6.86514 3.15788L10.6151 7.15788C10.7954 7.3502 10.7954 7.64949 10.6151 7.84182L6.86514 11.8418C6.67627 12.0433 6.35985 12.0535 6.1584 11.8646C5.95694 11.6757 5.94673 11.3593 6.1356 11.1579L9.565 7.49985L6.1356 3.84182C5.94673 3.64036 5.95694 3.32394 6.1584 3.13508Z" fill="currentColor" fill-rule="evenodd" clip-rule="evenodd"></path></svg><span class="rt-Text" style="font-family: var(--code-font-family); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0px; flex: 1 1 0%;">country_iso=AFG</span></a>
    # countries_to_cache = ["AFG", "AGO", "ALB", "AND" , "ARE", "ARG", "ARM", "ATG", "AUS"] # TODO: Get all dynamically from source_store listing
    countries_to_cache = ["AFG", "AUS"] # TODO: Get all dynamically from source_store listing
    logging.info(f"Found {len(countries_to_cache)} country files to cache from {source_location}. Caching...")

    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _download_parquet_file(
            source_location=source_location,
            country_iso=country_iso,
            target_store=target_store,
            overwrite=overwrite,
            semaphore=semaphore,
        )
        for country_iso in countries_to_cache
    ]
    await asyncio.gather(*tasks)
    logging.info("Completed downloading all country parquet files.")


# Buildings cache gets all parquet files from source_location, and downloads them to target_location.
# We need to get all 185 partitioned parquet files (per country).
# # https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country/country_iso=AFG/AFG.parquet
@buildings_app.command("cache")
def cache_buildings(
    source_location: str = typer.Option(
        "https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country",
        help="HTTP url containing parquet files to cache (e.g. https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write cached files (e.g. /tmp/buildings/0-0-1/data)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite files if they exist at target."),
    max_concurrent: int = typer.Option(16, help="Maximum number of caches to process at once."),
) -> None:
    logging.info("Starting buildings caching process ...")
    source_location = source_location.rstrip("/")
    target_location = target_location.rstrip("/")
    asyncio.run(_run_cache_buildings(source_location, target_location, overwrite, max_concurrent))
    logging.info("Buildings dataset caching process completed.")

async def _run_index_buildings(
    source_location: str,
    target_location: str,
    overwrite: bool,
) -> None:
    # target_store = get_store_with_prefix_from_url(target_location)
    # target_file_name = "buildings.parquet"
    # if exists(target_store, target_file_name) and not overwrite:
    #     logging.info(f"Skipping index: {target_file_name} already exists and overwrite is off.")
    #     return
    # source_store = get_store_with_prefix_from_url(source_location)
    # logging.info(f"Searching for all parquet files under {source_location} ...")
    # parquet_paths = find_matching_files(source_store, r"\.parquet$")
    # logging.info(f"Found {len(parquet_paths)} parquet files to merge into {target_file_name}")

    # TODO: Check if we can search to get bbox without havinf to download or load all data into memory. This would make cache step redundant and improve performance greatly.

    # Actually, why cache or index at all? We can just search the source location directly using Sedona db on the source co-op data.

    # sd = sedona.db.connect()
    # sd.read_parquet(source_location, options={"aws.skip_signature": True, "aws.region": aws_region}).to_view("geometries", overwrite=True)
    # geometry = sd.sql(f"SELECT st_srid(geometry) as crs, geometry, \"csdr-id\" FROM geometries WHERE \"csdr-id\" = '{geometry_id}'").to_pandas()
    # if len(geometry) == 0:

    # output = []
    # for path in parquet_paths:
    #     df = []
    #     # Get the name, path, and bounds.
    #     df.append("source_file", path)
    #     df.append("source_file", name)
        
    #     # Load data into memory
    #     # TODO: Use io.read_geospatial_file
    #     bytes_obj = source_store.get(path).bytes()
    #     # Get bbox
    #     df.append("source_file", path)

    #     # Add to dfs
    #     output.append(df)

    # logging.info(f"Writing index buildings parquet to {target_file_name}")
    # with BytesIO() as f:
    #     output.to_parquet(f, index=False)
    #     f.seek(0)
    #     target_store.put(target_file_name, f.read())
    logging.info("Index buildings dataset completed.")


# Buildings Index gets all of the parquet files (one per country), and merges them into a single buildings.parquet file at target_location.
@buildings_app.command("index")
def index_buildings(
    source_location: str = typer.Option(
        ...,
        help="S3 or local path to extracted buildings files (e.g. s3://bucket/datasets/buildings/0-0-1/data)"
    ),
    target_location: str = typer.Option(
        ...,
        help="S3 or local path to write buildings.parquet index (e.g. s3://bucket/datasets/buildings/0-0-1/)"
    ),
    overwrite: bool = typer.Option(True, help="Overwrite output file if it exists."),
) -> None:
    logging.info("Starting buildings index process ...")
    asyncio.run(_run_index_buildings(source_location, target_location, overwrite))
    logging.info("Index buildings dataset process completed.")

if __name__ == "__main__":
    buildings_app()
