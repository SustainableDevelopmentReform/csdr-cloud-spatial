import asyncio
import logging
import subprocess
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, TypedDict, cast

import boto3
import geopandas as gpd
import pystac
import rioxarray  # DO NOT REMOVE: Required to enable rioxarray extension for xarray (for .rio accessor and reproject)
import rustac
from affine import Affine
from odc.geo.geobox import GeoBox, GeoboxTiles
from odc.geo.geom import Geometry
from odc.geo.xr import mask
from odc.stac import load
from xarray import DataArray, Dataset

from csdr.io import get_store_with_prefix_from_url, split_path_and_file_name_from_url


class Event(TypedDict):
    timestamp: int
    message: str
    ingestionTime: int

# What are these grids? They are not used internally anywhere.
WGS84GRID10 = GeoboxTiles(
    GeoBox(
        (1800000, 3600000), Affine(0.0001, 0.0, -180.0, 0.0, 0.0001, -90.0), "epsg:4326"
    ),
    (5000, 5000),
)
WGS84GRID30 = GeoboxTiles(
    GeoBox(
        (600000, 1200000), Affine(0.0003, 0.0, -180.0, 0.0, 0.0003, -90.0), "epsg:4326"
    ),
    (5000, 5000),
)


# Submit a batch job
def submit_job(
    job_name: str,
    job_queue: str,
    job_definition: str,
    container_overrides: dict[str, Any],
    parameters: dict[str, str],
    multi: bool = False,
    multi_size: int = 30,  # This is how many tiles there are in each year
) -> str:
    """Submit a job to AWS Batch"""
    client = boto3.client("batch")
    extras = {}
    if multi:
        extras["arrayProperties"] = {"size": multi_size}

    response: dict[str, str] = client.submit_job(
        jobName=job_name,
        jobQueue=job_queue,
        jobDefinition=job_definition,
        containerOverrides=container_overrides,
        parameters=parameters,
        schedulingPriorityOverride=99,
        shareIdentifier="alex",
        retryStrategy={"attempts": 1},
        **extras,
    )
    return response["jobId"]


# Get the status of a job
def get_job_status(job_id: str) -> str:
    """Get the status of a job"""
    client = boto3.client("batch")
    response = client.describe_jobs(jobs=[job_id])
    return cast(str, response["jobs"][0]["status"])


def get_cloudwatch_logs(
    job_id: str, log_group_name: str = "/aws/batch/auspatious-csdr"
) -> Event:
    """Get the logs for a job"""
    client = boto3.client("batch")
    response = client.describe_jobs(jobs=[job_id])
    log_stream_name = response["jobs"][0]["container"]["logStreamName"]

    logs_client = boto3.client("logs")

    response = logs_client.get_log_events(
        logGroupName=log_group_name, logStreamName=log_stream_name, startFromHead=True
    )

    return cast(Event, response["events"])


def execute(year: int, tile: tuple[int, int] | None = None) -> str:
    """Submit one or a set of jobs to AWS Batch"""
    extra_params = []
    if tile is not None:
        multi = False
        extra_params = ["--tile", ",".join([str(t) for t in tile])]

    job_name = f"version-0-1-0-{year}"
    job_queue = "normalQueue"
    job_definition = "auspatious-csdr"
    container_overrides = {
        "command": [
            "csdr-processor",
            "--year",
            "Ref::year",
            "--version",
            "Ref::version",
            "--n-workers",
            "Ref::n_workers",
            "--threads-per-worker",
            "Ref::threads_per_worker",
            "--memory-limit",
            "Ref::memory_limit",
            "Ref::overwrite",
            *extra_params,
        ],
        "vcpus": 16,
        "memory": 122880,
    }
    parameters = {
        "tile": "238,47",
        "year": f"{year}",
        "version": "0.1.0",
        "n_workers": "4",
        "threads_per_worker": "32",
        "memory_limit": "100GB",
        "overwrite": "--no-overwrite",
    }

    job_id = submit_job(
        job_name,
        job_queue,
        job_definition,
        container_overrides,
        parameters,
        multi=multi,
    )
    return job_id


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


def read_stacgeoparquet(dataset_url: str) -> pystac.ItemCollection:
    # This could be refactored to use rustac.DuckdbClient.search so that less data is loaded however it gets errors sometimes such as "RustacError: External error: General error: Invalid byte order"
    path, file_name = split_path_and_file_name_from_url(dataset_url)
    store = get_store_with_prefix_from_url(path)
    
    async def _rustac_read() -> dict[str, Any]:
        stac_items = await rustac.read(file_name, store=store)
        print(f"Read {len(stac_items['features'])} STAC items from {dataset_url}")
        return stac_items
    
    stac_items = asyncio.run(_rustac_read())

    return pystac.ItemCollection.from_dict(stac_items)


def load_xarray_stacgeoparquet(
    items: pystac.ItemCollection,
    geometry: Geometry,
    datetime_string_match: str | None = None,
    **load_kwargs: dict[str, Any],
) -> Dataset:
    # Temporal filter (if parameter is provided)
    if datetime_string_match is not None:
        all_items = items.clone()
        items = []
        for item in all_items:
            if datetime_string_match in item.datetime.isoformat():
                items.append(item)

    # Force the use of Dask. Redundant because it is already done in get_area_from_dataset_geometry (parent function).
    if "chunks" not in load_kwargs:
        load_kwargs["chunks"] = {}

    # load_kwargs.resolution units must match CRS. We should check this. We are passing 10 (meters) for example but the units could be degrees if CRS is geographic.
    # ODC STAC load
    data = load(items, geopolygon=geometry, **load_kwargs)

    return data


def xarray_calculate_area(
    data: Dataset | DataArray,
    geom: Geometry,
    variable: str | None = None,
    value: int | float | None = None,
) -> float:
    # Work with a dataarray, not a dataset, so it's a singular thing
    if type(data) is not DataArray:
        if variable is None:
            raise ValueError("Variable must be specified when data is a Dataset.")
        data = data[variable]

    # Only select a specific value. This will convert to float, with nans
    if value is not None:
        data = data.where(data == value)

    # Validate that data and geom have the same CRS.
    target_crs = "EPSG:6933" # For consistency with all datasets and geometries
    data = data.rio.reproject(target_crs) # TODO: Tweak parameters of reproject: resolution=desired_resolution, method='nearest', resampling=Resampling.bilinear
    geom = geom.to_crs(target_crs)

    # Mask out regions outside the geometry
    masked = mask(data, geom)

    # Count all the non-nan cells, and multiply by area
    count = float(masked.notnull().sum().values)
    one_pixel_area = abs(
        masked.odc.geobox.resolution.x * masked.odc.geobox.resolution.y
    )

    return round(float(count) * one_pixel_area, 2)


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
        raise ValueError(f"Geometry ID {geometry_id} not found in GeoDataFrame.")
    if len(features) > 1:
        raise ValueError(f"Geometry ID {geometry_id} is not unique in GeoDataFrame.")
    feature = features.iloc[0]

    # Convert to ODC geometry
    return Geometry(feature.geometry, crs=gdf.crs)
