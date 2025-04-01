import typer
import xarray as xr
import logging
import rioxarray

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

dataset_app = typer.Typer()


@dataset_app.command("validate-zarr")
def validate_zarr(zarr_path: str = typer.Option(
        ..., "--input-zarr",
        help="Path to the Zarr file to validate."
    ),
):
    logger.info(f"Validating Zarr {zarr_path}...")

    xr.open_zarr(zarr_path)

    # Add actual validation logic here
    pass


@dataset_app.command("raster-to-zarr")
def raster_to_zarr(
    raster_path: str = typer.Option(
        ...,
        "--input-raster",
        help=(
            "Path to the input raster file (any format supported by rasterio)."
        )
    ),
    zarr_path: str = typer.Option(
        ..., "--output-zarr", help="Path for the output Zarr store."
    ),
):
    """Converts a raster file to Zarr, ensuring spatial dims are X and Y."""
    logger.info(f"Converting {raster_path} to Zarr {zarr_path}...")
    try:
        rds = rioxarray.open_rasterio(
            raster_path, masked=True, default_name="data_variable")
        logger.info("Opened raster file successfully.")

        # Ensure spatial dimensions are named x and y
        if 'X' in rds.dims and 'Y' in rds.dims:
            rds = rds.rename({'X': 'x', 'Y': 'y'})
            logger.info("Renamed spatial dims 'X'/'Y' to 'x'/'y'.")

        if 'x' not in rds.dims and 'y' not in rds.dims:
            logger.error(
                "No standard spatial dims ('x'/'y' or 'X'/'Y') found.")
            raise typer.Exit(code=1)

        logger.info(f"Writing Zarr: {zarr_path}")
        rds.to_zarr(zarr_path, mode='w', consolidated=True)
        logger.info("Successfully wrote Zarr store.")

    except Exception as e:
        logger.error(f"Raster to Zarr conversion failed: {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    dataset_app()
