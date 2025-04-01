import os
import typer
import xarray as xr
import geopandas as gpd
import logging
from typing import List, Optional
import xvec


# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

vector_cube_app = typer.Typer()


@vector_cube_app.command("zonal-stats")
def zonal_stats(
    zarr_path: str = typer.Option(
        ..., "--input-zarr",
        help="Path to the input Zarr dataset."
    ),
    geoparquet_path: str = typer.Option(
        ..., "--input-geoparquet",
        help="Path to the input GeoParquet file containing geometries."
    ),
    output_path: str = typer.Option(
        ..., "--output-path", "-o",
        help="Output path for the GeoParquet file with calculated statistics."
    ),
    data_variable: str = typer.Option(
        ..., "--data-variable",
        help="Name of the data variable within the Zarr dataset to analyze."
    ),
    stats: List[str] = typer.Option(
        ..., "--stat",
        help="Statistic to calculate (e.g., 'mean', 'sum'). Can be specified multiple times."
    ),
    output_crs: str = typer.Option(
        "EPSG:4326", "--output-crs",
        help="Target CRS for the final output GeoParquet file."
    ),
    name_dim: str = typer.Option(
        "geometry_dim", "--name-dim",
        help="Name for the dimension created during zonal stats, representing the geometries."
    ),
    fill_value: Optional[float] = typer.Option(
        0, "--fill-value",
        help="Value to fill NaN/nodata in the Zarr array before stats. Set to None to disable filling."
    )
):
    """
    Calculates zonal statistics from a Zarr dataset based on geometries
    from a GeoParquet file and outputs the results to a new GeoParquet file.
    """
    if not zarr_path or not geoparquet_path or not output_path or not data_variable or not stats:
        logger.error(
            "--input-zarr, --input-geoparquet, --output-path, --data-variable, and at least one --stat are required."
        )
        raise typer.Exit(code=1)

    try:
        logger.info(f"Reading Zarr dataset from: {zarr_path}")
        ds = xr.open_zarr(zarr_path, decode_coords="all")

        if data_variable not in ds:
            logger.error(
                f"Data variable '{data_variable}' not found in Zarr dataset.")
            logger.error(f"Available variables: {list(ds.data_vars)}")
            raise typer.Exit(code=1)

        da = ds[data_variable]  # Select the data variable

        logger.info(f"Reading GeoParquet geometries from: {geoparquet_path}")
        gdf_orig = gpd.read_parquet(geoparquet_path)

        # Filter out rows without geoms (important for zonal_stats)
        gdf_filtered = gdf_orig[gdf_orig.geometry.notnull()].copy()
        if len(gdf_filtered) != len(gdf_orig):
            logger.warning(
                f"Removed {len(gdf_orig) - len(gdf_filtered)} rows with null geometries.")
        if len(gdf_filtered) == 0:
            logger.error("No valid geometries found in the input GeoParquet.")
            raise typer.Exit(code=1)
        target_crs = da.rio.crs
        if not target_crs:
            logger.error("Could not determine CRS from Zarr dataset.")
            raise typer.Exit(code=1)
        logger.info(f"Using target CRS from Zarr: {target_crs}")

        logger.info(
            f"Reprojecting geometries from {gdf_filtered.crs} to {target_crs}")
        gdf_reprojected = gdf_filtered.to_crs(target_crs)

        # Crop the DataArray to the bounds of the reprojected geometries
        logger.info("Cropping Zarr data to geometry bounds...")
        minx, miny, maxx, maxy = gdf_reprojected.total_bounds
        # Ensure y slice is in correct order (max first for decreasing coordinates)
        da_cropped = da.sel(x=slice(minx, maxx), y=slice(maxy, miny))
        logger.info("Cropping complete.")

        # Fill NaN values if requested
        if fill_value is not None:
            logger.info(f"Filling NaN values with {fill_value}...")
            da_filled = da_cropped.fillna(fill_value)
        else:
            logger.info("Skipping NaN filling.")
            da_filled = da_cropped

        # Calculate zonal statistics
        logger.info(f"Calculating zonal statistics ({', '.join(stats)})...")
        stats_da = da_filled.xvec.zonal_stats(
            gdf_reprojected.geometry,
            x_coords="x",
            y_coords="y",
            stats=stats,
            name=name_dim,  # Use the specified name for the new dimension
            index=True  # Keep the original geometry index
        )
        logger.info(
            "Zonal statistics calculation complete. Computing results...")
        stats_computed = stats_da.compute()  # Compute the dask array
        logger.info("Computation complete.")

        # --- 4. Format and Write Output ---
        logger.info("Formatting results into GeoDataFrame...")
        # Convert the DataArray results to a GeoDataFrame
        stats_gdf = stats_computed.xvec.to_geodataframe()

        # Merge the statistics back into the *original* filtered GeoDataFrame
        # Use left_index and right_index because zonal_stats preserves the index
        stats_gdf = stats_gdf.merge(
            gdf_filtered,
            left_on="index", right_index=True
        )

        # Reproject final output if needed
        if stats_gdf.crs != output_crs:
            logger.info(f"Reprojecting final output to {output_crs}")
            stats_gdf = stats_gdf.to_crs(output_crs)

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Write out the final GeoParquet
        logger.info(f"Writing final GeoParquet to: {output_path}")
        stats_gdf.to_parquet(output_path)
        logger.info("Processing complete.")

    except Exception as e:
        logger.exception(f"An error occurred during zonal statistics: {e}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    vector_cube_app()
