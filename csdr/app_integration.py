import os

import requests
from requests import Response

HOSTNAME = os.getenv("CSDR_API_HOSTNAME", "http://localhost:4000").rstrip("/")
API_KEY = os.getenv("CSDR_API_KEY", None)


def _check_api_key() -> None:
    if API_KEY is None:
        raise ValueError(
            "API key must be provided in CSDR_API_KEY environment variable"
        )


def _post(url: str, json: dict) -> Response:
    _check_api_key()

    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/json",
    }

    return requests.post(url, json=json, headers=headers)


def post_provenance(
    provenance: dict[str, str | int], type: str = "dataset"
) -> Response:
    # Check for API key
    _check_api_key()

    if type not in ("dataset", "geometry"):
        raise ValueError("Type must be 'dataset' or 'geometry'")

    if type == "geometry":
        path = "api/v0/geometries-run"
        # Change id to geometryId
        provenance["geometriesId"] = provenance.pop("id")
    elif type == "dataset":
        path = "api/v0/dataset-run"
        # Change id to datasetId
        provenance["datasetId"] = provenance.pop("id")

    url = f"{HOSTNAME}/{path}"

    return _post(url, provenance)


def post_geometry_output_bulk(bulk_geometry_output: dict) -> Response:
    # Check for API key
    _check_api_key()

    url = f"{HOSTNAME}/api/v0/geometry-output/bulk"

    return _post(url, bulk_geometry_output)


def post_geometry_output(geometry_output: dict) -> Response:
    # Check for API key
    _check_api_key()

    url = f"{HOSTNAME}/api/v0/geometry-output"

    return _post(url, geometry_output)
