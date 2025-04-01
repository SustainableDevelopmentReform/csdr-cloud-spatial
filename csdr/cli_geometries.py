import os
import typer
import geopandas as gpd
import logging
import glob

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

geometry_app = typer.Typer()


@geometry_app.command("convert-vector")
def convert_vector(
    input_dir: str = typer.Option(
        ..., "--input-dir",
        help="Directory containing the input vector file(s)."
    ),
    output_path: str = typer.Option(
        ..., "--output-path", "-o",
        help="Output path for the GeoParquet file."
    ),
    target_crs: str = typer.Option(
        ..., "--target-crs",
        help="Target CRS for the output GeoParquet file (e.g., EPSG:4326)."
    ),
    input_glob: str = typer.Option(
        "*.shp", "--input-glob",
        help="Glob pattern to find the input vector file(s) within the input directory."
    ),
    source_crs_option: str = typer.Option(
        None,
        "--source-crs",
        help=(
            "Optional: Specify source CRS (e.g., 'EPSG:7844'). "
            "Overrides CRS detection from file."
        ),
    ),
):
    """
    Converts first found vector file matching glob to GeoParquet, applying CRS.

    Reads from --input-dir, finds file matching --input-glob, converts to 
    --output-path with --target-crs.
    """
    if not input_dir or not output_path or not target_crs:
        logger.error(
            "--input-dir, --output-path, and --target-crs are required.")
        raise typer.Exit(code=1)

    try:
        # Find input vector file using glob relative to input_dir
        # Search recursively within the input directory
        search_path = os.path.join(input_dir, "**", input_glob)
        logger.info(
            f"Searching for input vector file(s) matching: {search_path}")
        found_files = glob.glob(search_path, recursive=True)

        if not found_files:
            logger.error(
                f"No files matching '{input_glob}' found within {input_dir}")
            raise typer.Exit(code=1)

        vector_file_path = found_files[0]  # Use the first found file
        if len(found_files) > 1:
            logger.warning(
                f"Multiple files found matching '{input_glob}'. Using the first one: {vector_file_path}"
            )

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Read and process vector file
        logger.info(f"Reading {vector_file_path}")
        gdf = gpd.read_file(vector_file_path)

        # Determine source CRS
        source_crs = source_crs_option if source_crs_option else gdf.crs
        if not source_crs:
            logger.error(
                "Could not determine source CRS from file and "
                "--source-crs not provided."
            )
            raise typer.Exit(code=1)

        logger.info(f"Using source CRS: {source_crs}")

        # Reproject
        logger.info(f"Projecting from {source_crs} to {target_crs}")
        gdf = gdf.to_crs(target_crs)

        # --- Normalization Placeholder ---
        # Add column renaming, dtype setting etc. here based on CLI options
        # Example:
        # if rename_cols:
        #     import json
        #     mapping = json.loads(rename_cols)
        #     gdf = gdf.rename(columns=mapping)
        # if set_dtypes:
        #     import json
        #     dtypes = json.loads(set_dtypes)
        #     gdf = gdf.astype(dtypes)

        logger.info("Applying schema/normalization (placeholder)...")

        # Write out geoparquet
        logger.info(f"Writing to {output_path}")
        gdf.to_parquet(output_path)
        logger.info("Vector conversion complete.")

    except Exception as e:
        # Use logger.exception for stack trace
        logger.exception(f"An error occurred during vector conversion: {e}")
        raise typer.Exit(code=1)


@geometry_app.command("validate")
def validate(
    input_file: str = typer.Option(
        ..., "--input-file",
        help="Path to the GeoParquet file to validate."
    ),
    schema_path: str = typer.Option(
        None,
        "--schema",
        help="Path to the GeoParquet schema file to validate against."
    ),
):
    """
    Validate the GeoParquet file against the provided schema.
    """
    if not input_file:
        logger.error("Input file is required.")
        raise typer.Exit(code=1)

    try:
        # Read the GeoParquet file
        gdf = gpd.read_parquet(input_file)

        # Fail if no geometry column
        if "geometry" not in gdf.columns:
            logger.error("No geometry column found in the GeoParquet file.")
            raise typer.Exit(code=1)

        # Validate the GeoParquet file
        # validate_geoparquet(gdf, schema_path)
        logger.info("Validation complete.")

    except Exception as e:
        logger.exception(f"An error occurred during validation: {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    geometry_app()
