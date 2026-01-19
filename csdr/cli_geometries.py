import asyncio
import glob
import logging
import os
from io import BytesIO

import geopandas as gpd
import typer
from requests import get

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
)
from csdr.utils import CSDRException

geometry_app = typer.Typer()


@geometry_app.command("convert-vector")
def convert_vector(
    input_dir: str = typer.Option(
        ..., "--input-dir", help="Directory containing the input vector file(s)."
    ),
    output_path: str = typer.Option(
        ..., "--output-path", "-o", help="Output path for the GeoParquet file."
    ),
    target_crs: str = typer.Option(
        ...,
        "--target-crs",
        help="Target CRS for the output GeoParquet file (e.g., EPSG:4326).",
    ),
    input_glob: str = typer.Option(
        "*.shp",
        "--input-glob",
        help="Glob pattern to find the input vector file(s) within the input directory.",
    ),
    name_property: str = typer.Option(
        None,
        "--name-property",
        help="Name of the property to use as the name of the feature.",
    ),
    source_crs_option: str = typer.Option(
        None,
        "--source-crs",
        help=(
            "Optional: Specify source CRS (e.g., 'EPSG:7844'). "
            "Overrides CRS detection from file."
        ),
    ),
) -> None:
    """
    Converts first found vector file matching glob to GeoParquet, applying CRS.

    Reads from --input-dir, finds file matching --input-glob, converts to
    --output-path with --target-crs.
    """
    if not input_dir or not output_path or not target_crs:
        raise CSDRException("--input-dir, --output-path, and --target-crs are required.")

    try:
        # Find input vector file using glob relative to input_dir
        # Search recursively within the input directory
        search_path = os.path.join(input_dir, "**", input_glob)
        logging.info(f"Searching for input vector file(s) matching: {search_path}")
        found_files = glob.glob(search_path, recursive=True)

        if not found_files:
            raise CSDRException(f"No files matching '{input_glob}' found within {input_dir}")

        vector_file_path = found_files[0]  # Use the first found file
        if len(found_files) > 1:
            logging.warning(
                f"Multiple files found matching '{input_glob}'. Using the first one: {vector_file_path}"
            )

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Read and process vector file
        logging.info(f"Reading {vector_file_path}")
        # TODO: Use io.read_geospatial_file
        gdf = gpd.read_file(vector_file_path)

        # Determine source CRS
        source_crs = source_crs_option if source_crs_option else gdf.crs
        if not source_crs:
            raise CSDRException(
                "Could not determine source CRS from file and --source-crs not provided."
            )
        logging.info(f"Using source CRS: {source_crs}")

        # Reproject
        logging.info(f"Projecting from {source_crs} to {target_crs}")
        gdf = gdf.to_crs(target_crs)

        if name_property:
            gdf = gdf.rename(columns={name_property: "name"})

        logging.info("Applying schema/normalization (placeholder)...")

        # Write out geoparquet
        logging.info(f"Writing to {output_path}")
        gdf.to_parquet(output_path)
        logging.info("Vector conversion complete.")

    except Exception as e:
        raise CSDRException(f"An error occurred during vector conversion: {e}")


@geometry_app.command("validate")
def validate(
    input_file: str = typer.Option(
        ..., "--input-file", help="Path to the GeoParquet file to validate."
    ),
    schema_path: str = typer.Option(
        None, "--schema", help="Path to the GeoParquet schema file to validate against."
    ),
) -> None:
    """
    Validate the GeoParquet file against the provided schema.
    """
    if not input_file:
        raise CSDRException("Input file is required.")

    try:
        # Read the GeoParquet file
        # TODO: Use io.read_geospatial_file
        gdf = gpd.read_parquet(input_file)

        # Fail if no geometry column
        if "geometry" not in gdf.columns:
            raise CSDRException("No geometry column found in the GeoParquet file.")

        # Validate the GeoParquet file
        # validate_geoparquet(gdf, schema_path)
        logging.info("Validation complete.")

    except Exception as e:
        raise CSDRException(f"An error occurred during validation: {e}")
    

# Caching CWA and ACSC2 geometries
# "GA Coastal Waters Areas", or "Australian Coastal Sediment Compartments - Secondary Compartments"
async def run_cache(
    source_url: str,
    target_location: str,
    overwrite: bool,
) -> str:
    # Downloads the cwa/acsc2 zip of shapefile from source_url and stores it at target_location
    # Source url is http://
    # Target location can be s3:// or local file path
    target_location = target_location.rstrip("/")
    target_path = target_location # This is the path, there is no file name

    # Get the name of the file
    if "2af87180973d44b0b5b73583e3c06957_2" in source_url:
        logging.info("Detected ACSC2 geometry based on source URL.")
        target_file_name = "Australian_Coastal_Sediment_Compartments_-_Secondary_Compartments.zip"
    elif "37a401e932544c88828a7d099880afb5_1" in source_url:
        logging.info("Detected CWA geometry based on source URL.")
        target_file_name = "CW_1970_1980_Areas.zip"
    else:
        logging.error("Unknown geometry source URL.")
        raise CSDRException("Unknown geometry source URL.")

    target_store = get_store_with_prefix_from_url(target_path)

    if exists(target_store, target_file_name) and not overwrite:
        logging.info("File already exists at target location and overwrite is off, skipping download.")
        raise typer.Exit(code=0)  # Exit successfully, nothing to do

    logging.info("File doesn't exist or overwrite is on. Re-downloading.")

    logging.info(f"Downloading {target_file_name} from {source_url} to {target_location}...")

    # Download zip
    response = get(source_url)
    response.raise_for_status()  # Raise an error if the download failed
    zip_bytes = BytesIO(response.content)

    # TODO: Should get the file name dynamically here, but that would mean that the overwrite functionality would not be as effective in preventing re-downloads.

    # Get the file name
    await target_store.put_async(target_file_name, zip_bytes)

    return target_location


# Download zipped shapefile.
@geometry_app.command("cache")
def cache(
    source_url: str = typer.Option(
        ...,
        help="URL of the source zipped shapefile geometry to cache.",
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or remote path (like 's3://csdr-public-dev/geometries/acsc2/0-0-1/raw' or s3://csdr-public-dev/geometries/cwa/0-0-1/raw) to store the cached geometry file.",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing zip file if it exists."
    ),
) -> None:
    logging.info(f"Starting caching process for '{source_url}'...")

    result_path = asyncio.run(run_cache(source_url, target_location, overwrite))
    logging.info(f"Caching process completed. Cached to '{result_path}'")


if __name__ == "__main__":
    geometry_app()
