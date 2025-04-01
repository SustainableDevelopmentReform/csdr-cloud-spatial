from typing import Any, Dict

import boto3
from affine import Affine
from odc.geo.geobox import GeoBox, GeoboxTiles
import requests
import zipfile
import logging

WGS84GRID10 = GeoboxTiles(
    GeoBox(
        (1800000, 3600000),
        Affine(0.0001, 0.0, -180.0, 0.0, 0.0001, -90.0),
        "epsg:4326"),
    (5000, 5000),)
WGS84GRID30 = GeoboxTiles(
    GeoBox(
        (600000, 1200000),
        Affine(0.0003, 0.0, -180.0, 0.0, 0.0003, -90.0),
        "epsg:4326"),
    (5000, 5000),)


# Submit a batch job
def submit_job(
    job_name: str,
    job_queue: str,
    job_definition: str,
    container_overrides: Dict[str, Any],
    parameters: Dict[str, str],
    multi: bool = False,
    multi_size: int = 30,  # This is how many tiles there are in each year
) -> str:
    """Submit a job to AWS Batch"""
    client = boto3.client("batch")
    extras = {}
    if multi:
        extras["arrayProperties"] = {"size": multi_size}

    response = client.submit_job(
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
    return response["jobs"][0]["status"]


def get_cloudwatch_logs(
    job_id: str, log_group_name: str = "/aws/batch/auspatious-csdr"
) -> Dict[str, Any]:
    """Get the logs for a job"""
    client = boto3.client("batch")
    response = client.describe_jobs(jobs=[job_id])
    log_stream_name = response["jobs"][0]["container"]["logStreamName"]

    logs_client = boto3.client("logs")

    response = logs_client.get_log_events(
        logGroupName=log_group_name, logStreamName=log_stream_name,
        startFromHead=True)

    return response["events"]


def execute(year: int, tile: tuple[int, int] | None = None):
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


# === File Handling Utilities ===

# Configure logging specifically for utils if needed, or rely on root logger
# Use __name__ to get 'csdr.utils' logger
util_logger = logging.getLogger(__name__)
# Example handler if you want separate logging configuration:
# handler = logging.StreamHandler()
# formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# handler.setFormatter(formatter)
# util_logger.addHandler(handler)
# util_logger.setLevel(logging.INFO)


def download_file(url: str, local_path: str):
    """Downloads a file from a URL to a local path."""
    util_logger.info(f"Downloading data from {url}...")
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()  # Raise exception for bad status codes
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        util_logger.info(f"Successfully downloaded to {local_path}")
    except requests.exceptions.RequestException as e:
        util_logger.error(f"Error downloading {url}: {e}")
        raise


def unzip_file(zip_path: str, extract_dir: str):
    """Unzips a file to a specified directory."""
    util_logger.info(f"Unzipping {zip_path} to {extract_dir}")
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)
        util_logger.info(f"Successfully unzipped to {extract_dir}")
    except zipfile.BadZipFile:
        util_logger.error(
            f"Error: {zip_path} is not a valid zip file or is corrupted."
        )
        raise
    except Exception as e:
        util_logger.error(f"Error unzipping {zip_path}: {e}")
        raise
