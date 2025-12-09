import logging
import subprocess
import uuid
import zipfile
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
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


def open_stacgeoparquet(dataset_url: str, geometry: Geometry, datetime_string_match: str | None = None) -> pystac.ItemCollection:
    """Opens a STAC GeoParquet file, filters the items, reads to Arrow, and returns an ItemCollection."""

    client = rustac.DuckdbClient(extensions=["aws"])
    # Handle AWS S3 authentication
    session = boto3.Session()
    credentials = session.get_credentials()
    creds = credentials.get_frozen_credentials()
    # TODO: Don't hardcode the region. Get it from boto3 session or environment.
    client.execute("""
        CREATE OR REPLACE SECRET secret (
            TYPE s3,
            PROVIDER config,
            KEY_ID ?,
            SECRET ?,
            REGION 'ap-southeast-2'
        );
    """, params=[creds.access_key, creds.secret_key])
    
    # collections = client.get_collections(dataset_url)
    # logging.info(f"Found {len(collections)} collections: {collections}")

    # What about just using pystac.ItemCollection.from_file? Then we can skip rustac entirely.
    # Or use rusctac.search instead of rustac.read so that we can filter by bbox and datetime directly.
    # Or use rustac.duckdb.search instead of rustac.duckdb.search_to_arrow so that we can pass the output directly to pystac.ItemCollection.from_dict.

    geometry_geojson = geometry.geojson()["geometry"]
    # table = client.search_to_arrow(dataset_url)
    if datetime_string_match is not None:
        # Make single year into date range for filtering
        # Year filter example: dt='2017-01-01T00:00:00Z/2017-12-31T23:59:59Z' # This works.
        year = int(datetime_string_match)
        start = datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
        end = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
        dt_filter = f"{start}/{end}"
    else:
        dt_filter = None

    stac_table = client.search_to_arrow(dataset_url, intersects=geometry_geojson, datetime=dt_filter)
    stac_df = gpd.GeoDataFrame.from_arrow(stac_table)
    logging.info(f"Found {len(stac_df)} STAC-GeoParquet items that intersect the geometry and match the datetime filter.")
    # import pdb; pdb.set_trace()
    stac_dict = stac_df.to_dict(orient="records")
    # item_collection_dict = {
    #     "type": "FeatureCollection",
    #     "features": stac_dict
    # }
    # return pystac.ItemCollection.from_dict(item_collection_dict)

    # # Problematic fields:
    # Feature 0 field 'stac_extensions' triggers error: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()
    # Feature 0 field 'proj:transform' triggers error: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()
    # Feature 0 field 'proj:bbox' triggers error: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()
    # Feature 0 field 'links' triggers error: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()
    # Feature 0 field 'proj:shape' triggers error: The truth value of an array with more than one element is ambiguous. Use a.any() or a.all()

    #     (Pdb) print(stac_df.dtypes)
    # type                                            object
    # stac_version                                    object
    # stac_extensions                                 object
    # id                                              object
    # proj:transform                                  object
    # proj:bbox                                       object
    # links                                           object
    # assets                                          object
    # collection                                      object
    # datetime           datetime64[us, Australia/Melbourne]
    # start_datetime     datetime64[us, Australia/Melbourne]
    # end_datetime       datetime64[us, Australia/Melbourne]
    # created            datetime64[us, Australia/Melbourne]
    # proj:epsg                                        int64
    # proj:shape                                      object
    # bbox                                            object
    # geometry                                      geometry
    # proj:geometry                                   object
    # dtype: object

    # import numpy as np
    # import pandas as pd
    # def clean_value(val):
    #     if isinstance(val, np.ndarray):
    #         return val.tolist()
    #     if isinstance(val, (pd.Timestamp, pd.DatetimeTZDtype)):
    #         return val.isoformat() if hasattr(val, 'isoformat') else str(val)
    #     if hasattr(val, 'tolist') and not isinstance(val, (str, bytes)):
    #         return val.tolist()
    #     if hasattr(val, 'item') and not isinstance(val, (str, bytes)):
    #         return val.item()
    #     return val

    # def clean_row(row):
    #     cleaned = {k: clean_value(v) for k, v in row.items()}
    #     # if 'properties' not in cleaned:
    #     #     cleaned['properties'] = {}
    #     return cleaned
    

    # stac_dict = [clean_row(row) for row in stac_df.to_dict(orient="records")]
    # stac_dict = [{**row, "properties": {**row}} for row in stac_df.to_dict(orient="records")]
    stac_dict1 = stac_dict[0]
    # stac_df2 = {
    #     "type": "FeatureCollection",
    #     "features": {
    #         id: stac_dict1["id"],
    #         type: stac_dict1["type"]
    #     }
    # }

    import numpy as np

    def clean_obj(obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, dict):
            return {k: clean_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean_obj(v) for v in obj]
        return obj

    pystac.Item(
        id=stac_dict1["id"],
        geometry=stac_dict1["geometry"], # GeoJSON geometry
        bbox=stac_dict1.get("bbox"),
        datetime=stac_dict1.get("datetime"),
        properties=stac_dict1.get("properties", {}), # Doesn't exist, but needed
        start_datetime=stac_dict1.get("start_datetime"),
        end_datetime=stac_dict1.get("end_datetime"),
        stac_extensions=stac_dict1.get("stac_extensions", []).tolist() if hasattr(stac_dict1.get("stac_extensions", []), "tolist") else [],
        # stac_extensions=[],
        collection=stac_dict1.get("collection"),
        assets=clean_obj(stac_dict1.get("assets", {})),
        extra_fields={},
        href=""
    )
    # item_collection_dict = {
    #     "type": "FeatureCollection",
    #     "features": stac_dict
    # }
    # # Debug: print types after cleaning
    # for i, feature in enumerate(item_collection_dict.get("features", [])):
    #     for k, v in feature.items():
    #         print(f"Feature {i} field '{k}' type: {type(v)}")
    # Now build ItemCollection
    return pystac.ItemCollection.from_dict(item_collection_dict)

    # # Map the fields from the dataframe to pystac Items
    # from shapely.geometry import mapping
    # items = []
    # for _, row in stac_df.iterrows():
    #     item = pystac.Item(
    #         id=row['id'],
    #         geometry=mapping(row['geometry']), # GeoJSON geometry
    #         bbox=row.get('bbox'),
    #         datetime=row.get('datetime'),
    #         properties=row.get('properties', {}), # Doesn't exist, but needed.
    #         stac_extensions=row.get('stac_extensions', []),
    #         collection=row.get('collection'),
    #         assets=row.get('assets', {}),
    #         start_datetime=row.get('start_datetime', None),
    #         end_datetime=row.get('end_datetime', None),
    #         extra_fields={"type": row.get('type', None), "stac_version": row.get('stac_version', None), "proj:epsg": row.get('proj:epsg', None), "proj:transform": row.get('proj:transform', None), "proj:shape": row.get('proj:shape', None), "created": row.get('created', None), "links": row.get('links', None)}
    #     )
    #     items.append(item)

    # item_collection = pystac.ItemCollection(items)
    # return item_collection


def load_xarray_stacgeoparquet(
    items: pystac.ItemCollection,
    bbox: Iterable[float] | None = None, # TODO: Remove.
    geom: Geometry | None = None, # TODO: Remove.
    datetime_string_match: str | None = None, # TODO: Remove.
    **load_kwargs: dict[str, Any],
) -> Dataset:
    # Date filter is redundant because it is already done in open_stacgeoparquet (upstream function).
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
    # Bbox and geom filters are redundant because they are already done in open_stacgeoparquet (upstream function).
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
