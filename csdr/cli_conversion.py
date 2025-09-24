from io import BytesIO

import geopandas as gpd
import typer
from fiona.io import ZipMemoryFile
from loguru import logger
from obstore.store import S3Store

from csdr.io import (
    exists,
    get_dataset_name_from_url,
    get_prefix,
    get_store_for_url,
    get_url_from_store_filename,
    read_geospatial_file,
)

conversion_app = typer.Typer()


@conversion_app.command("zip-to-parquet")
def convert_zipfile_to_parquet(
    source_zip_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to the zip file containing the geospatial data.",
        default="./cache/example.zip",
    ),
    source_internal_path_name: str = typer.Option(
        help="The internal path within the zip file to the data to extract.",
        default="example/example.shp",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the converted file.",
        default="./cache",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing parquet file if it exists."
    ),
) -> None:
    logger.info("Starting parquet conversion process...")

    assert source_zip_location.endswith(".zip"), "Source file must be a .zip file"

    store = get_store_for_url(source_zip_location)
    source_zip_name_path = get_dataset_name_from_url(store, source_zip_location)

    if not exists(store, source_zip_name_path):
        logger.error(
            f"Source zip file does not exist at {source_zip_location}. Cannot extract."
        )
        raise typer.Exit(code=1)
    else:
        logger.info(
            f"Source zip file found at {source_zip_location}, proceeding with extraction."
        )

    target_location = target_location.rstrip("/")

    # Set up the target store
    target_store = get_store_for_url(target_location)
    target_filename = source_internal_path_name.split("/")[-1].replace(
        ".shp", ".parquet"
    )
    if type(target_store) is S3Store:
        # S3Store needs the full path including prefix
        path = get_prefix(target_location)
        if path is not None:
            target_filename = f"{path}/{target_filename}"
    target_url = get_url_from_store_filename(target_store, target_filename)

    # Check if target file already exists
    if exists(target_store, target_filename) and not overwrite:
        logger.warning(
            f"Target parquet file already exists at {target_url}. Use --overwrite to replace."
        )
        raise typer.Exit(code=0)

    # Pull the whole zip into memory
    zip_bytes = BytesIO(store.get(source_zip_name_path).bytes())

    # Use Fiona's in-memory ZIP reader (works with bytes and includes all sidecar files)
    with ZipMemoryFile(zip_bytes) as z:
        # Open the shapefile within the ZIP
        with z.open(source_internal_path_name) as src:
            gdf = gpd.GeoDataFrame.from_features(src, crs=src.crs)

    logger.info(f"Loaded {len(gdf)} records from the shapefile.")

    # Write GeoDataFrame to a GeoParquet file in memory
    with BytesIO() as parquet_buffer:
        gdf.to_parquet(parquet_buffer, engine="pyarrow")
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        target_store.put(target_filename, parquet_buffer.getvalue())

    logger.info(f"Target store is {target_store}, filename is {target_filename}")

    logger.info(f"Parquet extraction process completed. Wrote file to {target_url}")


@conversion_app.command("geo-to-parquet")
def convert_geospatial_file_to_parquet(
    source_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to the geospatial file.",
        default="./tests/data/single_geometry.geojson",
    ),
    target_location: str | None = typer.Option(
        help="Local or remote path (file:// or s3://) to store the converted file.",
        default=None,
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing parquet file if it exists."
    ),
) -> None:
    logger.info("Starting geospatial to parquet conversion process...")

    store = get_store_for_url(source_location)
    source_name_path = get_dataset_name_from_url(store, source_location)

    if not exists(store, source_name_path):
        logger.error(
            f"Source geospatial file does not exist at {source_location}. Cannot convert."
        )
        raise typer.Exit(code=1)
    else:
        logger.info(
            f"Source geospatial file found at {source_location}, proceeding with conversion."
        )
    if target_location is None:
        target_location = source_location

    # Set up the target store
    target_store = get_store_for_url(target_location)
    target_filename = source_name_path.split("/")[-1].rsplit(".", 1)[0] + ".parquet"
    if type(target_store) is S3Store:
        # S3Store needs the full path including prefix
        path = get_prefix(target_location)
        if path is not None:
            target_filename = f"{path}/{target_filename}"
    target_url = get_url_from_store_filename(target_store, target_filename)

    # Check if target file already exists
    if exists(target_store, target_filename) and not overwrite:
        logger.warning(
            f"Target parquet file already exists at {target_url}. Use --overwrite to replace."
        )
        raise typer.Exit(code=0)

    # Read the geospatial file into a GeoDataFrame
    gdf = read_geospatial_file(store, source_name_path)

    logger.info(f"Opened file with {len(gdf)} features")

    with BytesIO() as parquet_buffer:
        gdf.to_parquet(parquet_buffer, engine="pyarrow")
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        target_store.put(target_filename, parquet_buffer.getvalue())

    logger.info(f"Loaded {len(gdf)} records from the geospatial file.")
    logger.info(f"Parquet conversion process completed. Wrote file to {target_url}")
