import logging
import subprocess
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import geopandas as gpd
import pystac
import rioxarray  # noqa: F401  # DO NOT REMOVE! Required to enable rioxarray extension for xarray (for .rio accessor and reproject)
from odc.geo.geom import Geometry
from odc.geo.xr import mask
from odc.stac import load
from xarray import DataArray, Dataset


class CSDRException(Exception):
    pass


def run_command(command: list[str]) -> tuple[bool, str, str]:
    """Runs a shell command and returns success status, stdout, and stderr."""
    try:
        process = subprocess.run(
            command,
            check=False,  # Don't raise exception on non-zero exit
            capture_output=True,
            text=True,
            encoding="utf-8",  # Ensure consistent encoding
        )
        # Log stderr even on success, as it might contain warnings
        if process.stderr:
            cmd_str = " ".join(command)
            logging.debug(f"Command '{cmd_str}' stderr:")
            logging.debug(process.stderr.strip())

        if process.returncode == 0:
            return True, process.stdout.strip(), ""
        else:
            # Construct error message carefully to avoid f-string issues
            cmd_str = " ".join(command)
            stderr_str = process.stderr.strip()
            stdout_str = process.stdout.strip()
            error_message = (
                f"Command '{cmd_str}' failed with exit code {process.returncode}.\n"
                f"Stderr:\n{stderr_str}\n"
                f"Stdout:\n{stdout_str}"
            )
            logging.error(error_message)
            return False, stdout_str, stderr_str
    except FileNotFoundError:
        cmd_zero = command[0] if command else "<empty command>"
        logging.error(f"Command not found: {cmd_zero}")
        return False, "", f"Command not found: {cmd_zero}"
    except Exception as e:
        cmd_str = " ".join(command)
        logging.error(f"Failed to run command '{cmd_str}': {e}", exc_info=True)
        return False, "", str(e)


def load_xarray_stacgeoparquet(
    items: pystac.ItemCollection,
    **load_kwargs: dict[str, Any],
) -> Dataset:
    # Force the use of Dask. Redundant because it is already done in get_area_m2_from_dataset_geometry (parent function).
    if "chunks" not in load_kwargs:
        load_kwargs["chunks"] = {}

    # load_kwargs.resolution units must match CRS. We should check this. We are passing 10 (meters) for example but the units could be degrees if CRS is geographic.
    # ODC STAC load
    data = load(items, **load_kwargs)

    return data


def xarray_calculate_area_m2(
    data: Dataset | DataArray,
    geom: Geometry,
    indicator: str | None = None,
    value: int | float | None = None,
) -> float:
    # Work with a dataarray, not a dataset, so it's a singular thing
    if type(data) is not DataArray:
        if indicator is None:
            raise CSDRException("Indicator must be specified when data is a Dataset.")
        data = data[indicator]

    # Only select a specific value. This will convert to float, with nans
    if value is not None:
        data = data.where(data == value)

    # Validate that data and geom have the same CRS.
    target_crs = "EPSG:6933"  # For consistency with all datasets and geometries
    data = data.rio.reproject(
        target_crs
    )  # TODO: Tweak parameters of reproject: resolution=desired_resolution, method='nearest', resampling=Resampling.bilinear
    geom = geom.to_crs(target_crs)

    # Mask out regions outside the geometry
    masked = mask(data, geom)

    # Count all the non-nan cells, and multiply by area
    count = float(masked.notnull().sum().values)
    one_pixel_area_m2 = abs(
        masked.odc.geobox.resolution.x * masked.odc.geobox.resolution.y
    )

    return round(float(count) * one_pixel_area_m2, 2)


# Make a UUID
def make_uuid(thing: str) -> str:
    namespace = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial",
    )
    return str(uuid.uuid5(namespace, thing))


@contextmanager
def suppress_rust_output() -> Generator[None, None, None]:
    """Context manager to suppress all output from Rust code using OS-level redirection."""
    import os

    # Save original file descriptors
    stdout_fd = os.dup(1)
    stderr_fd = os.dup(2)

    try:
        # Redirect stdout and stderr to /dev/null (Unix) or NUL (Windows)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)  # Redirect stdout
        os.dup2(devnull, 2)  # Redirect stderr
        os.close(devnull)

        yield

    finally:
        # Restore original file descriptors
        os.dup2(stdout_fd, 1)
        os.dup2(stderr_fd, 2)
        os.close(stdout_fd)
        os.close(stderr_fd)


def get_geom_from_gdf(gdf: gpd.GeoDataFrame, geometry_id: str) -> Geometry:
    features = gdf[gdf["csdr-id"] == geometry_id]
    if len(features) == 0:
        raise CSDRException(f"Geometry ID {geometry_id} not found in GeoDataFrame.")
    if len(features) > 1:
        raise CSDRException(f"Geometry ID {geometry_id} is not unique in GeoDataFrame.")
    feature = features.iloc[0]

    # Convert to ODC geometry
    return Geometry(feature.geometry, crs=gdf.crs)
