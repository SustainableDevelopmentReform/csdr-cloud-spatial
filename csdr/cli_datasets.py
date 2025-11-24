import glob
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial

import rioxarray
import typer
import xarray as xr

from csdr.utils import run_command

dataset_app = typer.Typer()


@dataset_app.command("validate-zarr")
def validate_zarr(
    zarr_path: str = typer.Option(
        ..., "--input-zarr", help="Path to the Zarr file to validate."
    ),
) -> None:
    logging.info(f"Validating Zarr {zarr_path}...")

    xr.open_zarr(zarr_path)

    # Add actual validation logic here
    pass


def _run_gdalwarp(
    input_file: str,
    output_dir: str,
    target_crs: str,
    target_resolution: str,
    resampling_method: str,
) -> str | None:
    """Helper function to run gdalwarp for a single file."""
    base_name = os.path.basename(input_file)
    output_file = os.path.join(output_dir, base_name)

    cmd = [
        "gdalwarp",
        "-t_srs",
        target_crs,
        "-tr",
        target_resolution,
        target_resolution,
        "-r",
        resampling_method,
        input_file,
        output_file,
    ]

    warp_success, _, warp_stderr = run_command(cmd)
    if not warp_success:
        logging.error(f"Failed to warp {input_file} -> {output_file} - {warp_stderr}")
        return warp_stderr
    return None


@dataset_app.command("warp-raster")
def warp_raster(
    input_dir: str = typer.Option(
        ..., "--input-dir", help="Directory containing input raster files."
    ),
    output_dir: str = typer.Option(
        ..., "--output-dir", help="Directory for warped output raster files."
    ),
    target_crs: str = typer.Option(
        ..., "--target-crs", help="Target Coordinate Reference System (CRS)."
    ),
    target_resolution: str = typer.Option(
        ..., "--target-resolution", help="Target spatial resolution."
    ),
    resampling_method: str = typer.Option(
        "nearest", "--resampling-method", help="Resampling method to use."
    ),
    num_workers: int | None = typer.Option(
        None, "--num-workers", help="Number of parallel workers. Defaults to CPU count."
    ),
) -> None:
    """Warps raster files from an input directory to an output directory in parallel."""
    if num_workers is None:
        num_workers = multiprocessing.cpu_count()
        logging.info(f"Using default number of workers: {num_workers}")

    logging.info(
        f"Warping rasters from {input_dir} to {output_dir} using {num_workers} workers..."
    )
    try:
        os.makedirs(output_dir, exist_ok=True)
        logging.info(f"Ensured output directory exists: {output_dir}")

        # Assuming GeoTIFFs for now
        input_pattern = os.path.join(input_dir, "*.tif")
        input_files = glob.glob(input_pattern)

        if not input_files:
            logging.warning(f"No *.tif files found in {input_dir}")
            return

        logging.info(f"Found {len(input_files)} files to warp in {input_dir}")

        successful_warps = 0
        failed_warps = 0
        warp_func = partial(
            _run_gdalwarp,
            output_dir=output_dir,
            target_crs=target_crs,
            target_resolution=target_resolution,
            resampling_method=resampling_method,
        )

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(warp_func, infile): infile for infile in input_files
            }

            for future in as_completed(futures):
                input_file = futures[future]
                try:
                    result = future.result()
                    if result is None:
                        basename = os.path.basename(input_file)
                        logging.info(f"Successfully warped {basename}")
                        successful_warps += 1
                    else:
                        # Log the error message returned by the worker
                        logging.error(result)
                        failed_warps += 1
                except Exception as exc:
                    # Log the exception details, breaking the line
                    logging.error(
                        f"{input_file} generated an exception during processing: {exc}", exc_info=True
                    )
                    failed_warps += 1

        # Break the final log message to satisfy the linter
        log_msg = (
            f"Finished warping. Success: {successful_warps}, Failed: {failed_warps}"
        )
        logging.info(log_msg)
        if failed_warps > 0:
            logging.error(f"{failed_warps} files failed to warp.")
            # Optionally raise an error if any failures occurred
            raise typer.Exit(code=1)

    except Exception as e:
        logging.error(f"Raster warping process failed: {e}", exc_info=True)
        raise typer.Exit(code=1)


@dataset_app.command("raster-to-zarr")
def raster_to_zarr(
    raster_path: str = typer.Option(
        ...,
        "--input-raster",
        help=("Path to the input raster file (any format supported by rasterio)."),
    ),
    zarr_path: str = typer.Option(
        ..., "--output-zarr", help="Path for the output Zarr store."
    ),
) -> None:
    """Converts a raster file to Zarr, ensuring spatial dims are X and Y."""
    logging.info(f"Converting {raster_path} to Zarr {zarr_path}...")
    try:
        rds = rioxarray.open_rasterio(
            raster_path, masked=True, default_name="data_variable"
        )
        if not isinstance(rds, xr.DataArray):
            logging.error(f"Expected xarray.DataArray, got {type(rds).__name__}")
            raise typer.Exit(code=1)

        logging.info("Opened raster file successfully.")

        # Ensure spatial dimensions are named x and y
        if "X" in rds.dims and "Y" in rds.dims:
            rds = rds.rename({"X": "x", "Y": "y"})
            logging.info("Renamed spatial dims 'X'/'Y' to 'x'/'y'.")

        if "x" not in rds.dims and "y" not in rds.dims:
            logging.error("No standard spatial dims ('x'/'y' or 'X'/'Y') found.")
            raise typer.Exit(code=1)

        logging.info(f"Writing Zarr: {zarr_path}")
        rds.to_zarr(zarr_path, mode="w", consolidated=True)
        logging.info("Successfully wrote Zarr store.")

    except Exception as e:
        logging.error(f"Raster to Zarr conversion failed: {e}", exc_info=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    dataset_app()
