import asyncio
import logging
import subprocess
import uuid
import zipfile
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from typing import Any, TypedDict, cast

import boto3
import requests
import rustac
from affine import Affine
from geopandas import GeoDataFrame
from odc.geo.geobox import GeoBox, GeoboxTiles
from odc.geo.geom import BoundingBox, Geometry
from odc.geo.xr import mask
from odc.stac import load
from pystac import ItemCollection
from xarray import DataArray, Dataset

from csdr.io import get_prefix_file_name_from_url, get_store_from_url


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


def download_file(url: str, local_path: str) -> None:
    """Downloads a file from a URL to a local path."""
    logging.info(f"Downloading data from {url}...")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()  # Raise exception for bad status codes
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logging.info(f"Successfully downloaded to {local_path}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Error downloading {url}: {e}", exc_info=True)
        raise


def unzip_file(zip_path: str, extract_dir: str) -> None:
    """Unzips a file to a specified directory."""
    logging.info(f"Unzipping {zip_path} to {extract_dir}")
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)
        logging.info(f"Successfully unzipped to {extract_dir}")
    except zipfile.BadZipFile:
        logging.error(f"Error: {zip_path} is not a valid zip file or is corrupted.")
        raise
    except Exception as e:
        logging.error(f"Error unzipping {zip_path}: {e}", exc_info=True)
        raise


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


def open_stacgeoparquet(url: str) -> ItemCollection:
    """Opens a STAC GeoParquet file and returns an ItemCollection."""

    store = get_store_from_url(url)
    prefix_file_name = get_prefix_file_name_from_url(url)

    async def _read_stac_items_async() -> ItemCollection:
        return await rustac.read(prefix_file_name, store=store)
    
    # Check if we're already in an event loop (e.g., Jupyter notebook)
    # Does Argo/Dask also have an event loop running?
    try:
        asyncio.get_running_loop()
        # If we're in an event loop, we need to use nest_asyncio
        import nest_asyncio

        nest_asyncio.apply()
        item_dict = asyncio.run(_read_stac_items_async())
    except RuntimeError:
        # No event loop running, safe to use asyncio.run()
        item_dict = asyncio.run(_read_stac_items_async())

    return ItemCollection.from_dict(item_dict)


def load_xarray_stacgeoparquet(
    items: ItemCollection,
    bbox: Iterable[float] | None = None,
    geom: Geometry | None = None,
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

    # ODC STAC load 
    data = load(items, bbox=bbox, geopolygon=geom, **load_kwargs)

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

    # Mask out regions outside the geometry
    masked = mask(data, geom)

    # Count all the non-nan cells, and multiply by area
    count = float(masked.notnull().sum().values)
    one_pixel_area = abs(
        masked.odc.geobox.resolution.x * masked.odc.geobox.resolution.y
    )

    return float(count) * one_pixel_area


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


def get_geom_from_gdf(gdf: GeoDataFrame, geometry_id: str) -> Geometry:
    features = gdf[gdf["csdr-id"] == geometry_id]
    if len(features) == 0:
        raise ValueError(f"Geometry ID {geometry_id} not found in GeoDataFrame.")
    if len(features) > 1:
        raise ValueError(f"Geometry ID {geometry_id} is not unique in GeoDataFrame.")
    feature = features.iloc[0]

    # Convert to ODC geometry
    return Geometry(feature.geometry, crs=gdf.crs)

def check_for_any_intersection(geometry: Geometry, stac_items: ItemCollection) -> bool:
    # make geometry bbox
    geom_bbox = geometry.boundingbox.polygon # make a polygon from the bbox from the detailed geometry
    # Check CRS's match between geometry and stac items. Essential for intersection test.
    # TODO: Find a way to reproject data near the antimeridian robustly. Use a global CRS. Split geometries at the antimeridian before reprojecting. Check for validity after reprojection.
    geom_epsg = geom_bbox.crs.epsg
    stac_epsg = stac_items[0].properties.get("proj:code")
    stac_epsg_number = int(stac_epsg.replace("EPSG:",""))
    if geom_epsg != stac_epsg_number:
        logging.warning("CRS mismatch between geometry and STAC items. Reprojecting...")
        geom_bbox = geom_bbox.to_crs(stac_epsg)
    # Intersect geometry bbox with each STAC item bbox
    # If any intersect, return true
    # Else, return false
    for item in stac_items:
        # Either of these work. Either have to nest further or unnest it. I think nesting further is safer.
        # item_geometry = polygon(item.properties.get("proj:geometry")[0], item.properties.get('proj:code')) # this is either the bbox or the footprint of valid data
        # This code needs to handle coords that do not follow the right hand rule.
        # Bad : [[8.0, -1.0], [9.0, -1.0], [9.0, 0.0], [8.0, 0.0], [8.0, -1.0]]
        # Good: [[8.0, -1.0], [8.0, 0.0], [9.0, 0.0], [9.0, -1.0], [8.0, -1.0]]
        # Need to make the polygon because some of the proj:geometry values are not valid for future steps.
        # proj:geometry could be better because it is either the bbox or the footprint of valid data (more accurate than just bbox).
        item_bbox = BoundingBox(*item.properties.get("proj:bbox"), stac_epsg).polygon # This assumes all items have the same CRS

        if geom_bbox.intersects(item_bbox):
            return True
    return False
