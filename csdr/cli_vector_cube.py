import logging
import os

import dvc.api
import geopandas as gpd
import odc.geo.xr  # noqa: F401
import typer
import xarray as xr
import xvec  # noqa: F401


vector_cube_app = typer.Typer()


def parse_fill_value(value: str | None) -> float | None:
    if value is None:
        return None
    elif value.lower() == "none":
        return None
    return float(value)


@vector_cube_app.command("zonal-stats")
def zonal_stats(
    zarr_path: str = typer.Option(
        ..., "--input-zarr", help="Path to the input Zarr dataset."
    ),
    geoparquet_path: str = typer.Option(
        ...,
        "--input-geoparquet",
        help="Path to the input GeoParquet file containing geometries.",
    ),
    output_path: str = typer.Option(
        ...,
        "--output-path",
        "-o",
        help="Output path for the GeoParquet file with calculated statistics.",
    ),
    data_variable: str = typer.Option(
        ...,
        "--data-variable",
        help="Name of the data variable within the Zarr dataset to analyze.",
    ),
    output_crs: str = typer.Option(
        "EPSG:4326",
        "--output-crs",
        help="Target CRS for the final output GeoParquet file.",
    ),
    name_dim: str = typer.Option(
        "geometry_dim",
        "--name-dim",
        help="Name for the dimension created during zonal stats, "
        "representing the geometries.",
    ),
    fill_value: str | None = typer.Option(
        0,
        "--fill-value",
        callback=parse_fill_value,
        help="Value to fill NaN/nodata in the Zarr array before stats. "
        "Set to None to disable filling.",
    ),
    proj_crs: str | None = typer.Option(
        None, "--proj-crs", help="Projected CRS to use for area based statistics."
    ),
    decode_all: bool = typer.Option(
        True,
        "--decode-all/--no-decode-all",
        help="Decode all coordinates in the Zarr dataset. "
        "This can be useful for datasets with complex coordinate structures.",
    ),
) -> None:
    """
    Calculates zonal statistics from a Zarr dataset based on geometries
    from a GeoParquet file and outputs the results to a new GeoParquet file.
    """
    if not zarr_path or not geoparquet_path or not output_path or not data_variable:
        logging.error(
            "--input-zarr, --input-geoparquet, --output-path, and "
            "--data-variable are required."
        )
        raise typer.Exit(code=1)

    params_path = os.path.join(os.getcwd(), "params.yaml")
    params = dvc.api.params_show(params_path)
    if "zonal_stats" in params:
        stats = params["zonal_stats"]
    else:
        logging.error("zonal_stats must be specified in params.yaml.")
        raise typer.Exit(code=1)

    try:
        logging.info(f"Reading Zarr dataset from: {zarr_path}")

        options = {}
        if decode_all:
            options["decode_coords"] = "all"

        ds = xr.open_zarr(zarr_path, **options)

        if data_variable not in ds:
            logging.error(f"Data variable '{data_variable}' not found in Zarr dataset.")
            logging.error(f"Available variables: {list(ds.data_vars)}")
            raise typer.Exit(code=1)

        da = ds[data_variable]  # Select the data variable

        if proj_crs and da.odc.crs != proj_crs:
            logging.info(
                f"Reprojecting dataset from {da.odc.crs.to_wkt(pretty=True)} to {proj_crs}."
            )
            da = da.odc.reproject(proj_crs, resampling="nearest")
            da.name = data_variable  # name gets changed by reproject

        # Handle S3 vs local paths automatically via geopandas
        logging.info(f"Reading GeoParquet geometries from: {geoparquet_path}")
        if geoparquet_path.startswith("s3://"):
            gdf_orig = gpd.read_parquet(
                geoparquet_path,
            )
        else:
            gdf_orig = gpd.read_parquet(geoparquet_path)

        # Filter out rows without geoms (important for zonal_stats)
        gdf_filtered = gdf_orig[gdf_orig.geometry.notnull()].copy()
        if len(gdf_filtered) != len(gdf_orig):
            logging.warning(
                f"Removed {len(gdf_orig) - len(gdf_filtered)} rows "
                f"with null geometries."
            )
        if len(gdf_filtered) == 0:
            logging.error("No valid geometries found in the input GeoParquet.")
            raise typer.Exit(code=1)
        target_crs = da.rio.crs
        if not target_crs:
            logging.error("Could not determine CRS from Zarr dataset.")
            raise typer.Exit(code=1)
        logging.info(f"Using target CRS from Zarr: {target_crs}")

        logging.info(
            f"Reprojecting geometries from {gdf_filtered.crs.to_wkt(pretty=True)} to {target_crs}"
        )
        gdf_reprojected = gdf_filtered.to_crs(target_crs)

        # Crop the DataArray to the bounds of the reprojected geometries
        logging.info("Cropping Zarr data to geometry bounds...")
        minx, miny, maxx, maxy = gdf_reprojected.total_bounds

        # Extract coordinate names
        x_coord = "x" if "x" in da.coords else "lon"
        y_coord = "y" if "y" in da.coords else "lat"

        # Ensure y slice is in correct order
        da_cropped = da.sel(
            **{
                x_coord: slice(minx, maxx),
                y_coord: slice(maxy, miny),  # Note: max first for decreasing y
            }
        )
        logging.info("Cropping complete.")

        # Fill NaN values if requested
        if fill_value is not None:
            logging.info(f"Filling NaN values with {fill_value}...")
            da_filled = da_cropped.fillna(fill_value)
        else:
            logging.info("Skipping NaN filling.")
            da_filled = da_cropped

        # Calculate zonal statistics
        logging.info(
            f"Calculating zonal statistics ({[stat['stat'] for stat in stats]})"
        )
        if "classes" in params:
            classes = params["classes"]
            stat_arrays = []
            for c in classes:
                binary_mask = (da_filled == c).astype(int)
                temp_da = binary_mask.xvec.zonal_stats(
                    gdf_reprojected.geometry,
                    x_coords=x_coord,
                    y_coords=y_coord,
                    stats=[(stat["name"], stat["stat"]) for stat in stats],
                    name=name_dim,  # Use the specified name for the new dimension
                    index=True,  # Keep the original geometry index
                )
                temp_da = temp_da.expand_dims({"class": [c]})
                stat_arrays.append(temp_da)
            stats_da = xr.concat(stat_arrays, dim="class")
        else:
            stats_da = da_filled.xvec.zonal_stats(
                gdf_reprojected.geometry,
                x_coords=x_coord,
                y_coords=y_coord,
                stats=[(stat["name"], stat["stat"]) for stat in stats],
                name=name_dim,  # Use the specified name for the new dimension
                index=True,  # Keep the original geometry index
            )

        for stat in stats:
            # Calculate area-based statistics if requested:
            if stat["mode"] == "area":
                if not stats_da.rio.crs.is_projected:
                    logging.error(
                        "Data is in a geographic CRS, must specify a projected CRS for area-based statistics."
                    )
                    raise typer.Exit(code=1)
                else:
                    pixel_area = abs(
                        stats_da.rio.resolution()[0] * stats_da.rio.resolution()[1]
                    )
                    area_da = stats_da.sel(zonal_statistics=stat["name"]) * pixel_area
                    stats_da.loc[dict(zonal_statistics=stat["name"])] = area_da.values

        logging.info("Zonal statistics calculation complete. Computing results...")
        stats_computed = stats_da.compute()  # Compute the dask array
        logging.info("Computation complete.")

        # Format and Write Output
        logging.info("Formatting results into GeoDataFrame...")
        # Convert the DataArray results to a GeoDataFrame
        stats_gdf = stats_computed.xvec.to_geodataframe()
        stats_gdf = stats_gdf.reset_index()
        stats_gdf = stats_gdf.pivot(
            index=[
                col
                for col in stats_gdf.columns
                if col not in ["zonal_statistics", data_variable]
            ],
            columns="zonal_statistics",
            values=data_variable,
        )
        stats_gdf = stats_gdf.reset_index()
        # Merge the statistics back into the *original* filtered GeoDataFrame
        stats_gdf = stats_gdf.merge(gdf_filtered, left_on="index", right_index=True)
        # GeoDataFrame becomes a DataFrame after pivot, must make it a GeoDataFrame again
        stats_gdf = gpd.GeoDataFrame(
            stats_gdf, geometry="geometry", crs=gdf_filtered.crs
        )

        # Reproject final output if needed
        if stats_gdf.crs != output_crs:
            logging.info(f"Reprojecting final output to {output_crs}")
            stats_gdf = stats_gdf.to_crs(output_crs)

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Write out the final GeoParquet
        logging.info(f"Writing final GeoParquet to: {output_path}")
        stats_gdf.to_parquet(output_path)
        logging.info("Processing complete.")

    except Exception as e:
        logging.error(f"An error occurred during zonal statistics: {e}", exc_info=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    vector_cube_app()
