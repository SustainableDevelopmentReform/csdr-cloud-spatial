import os
from typing import Literal

import requests
from requests import Response

HOSTNAME = os.getenv("CSDR_API_HOSTNAME", "http://localhost:4000").rstrip("/")
API_KEY = os.getenv("CSDR_API_KEY", None)

ALLOWED_TYPES = ["dataset", "geometry", "product"]


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
    provenance: dict[str, str | int | dict[str, str | int]], type: Literal["dataset", "geometry", "product"] 
) -> Response:
    # Check for API key
    _check_api_key()

    if type not in ALLOWED_TYPES:
        raise ValueError(f"Type must be one of {ALLOWED_TYPES}")

    # IDs are reassigned based on type so that the primary/foreign keys are correct for the database
    if type == "geometry":
        path = "api/v0/geometries-run"
        # We are writing a provenance for a geometry so the id is actually the geometry run id, and the geometry id needs to be stored separately.
        # Change id to geometryId
        provenance["geometriesId"] = provenance.pop("id") # id is actually the geometry id.
        # Change runId to id if it exists
        geometriesRunId = provenance.pop("geometriesRunId", None)
        if geometriesRunId:
            provenance["id"] = geometriesRunId
        # Else if no geometriesRunId, then one will be assigned by the database
    elif type == "dataset":
        path = "api/v0/dataset-run"
        # Change id to datasetId
        # Should this restructuring happen before posting to database? Currently the DB version is different to the json file.
        provenance["datasetId"] = provenance.pop("id")
        # Datasets do not have a run ID by design.
    else:  # Product
        path = "api/v0/product-run"
        # Change id to productId
        provenance["productId"] = provenance.pop("id")
        # Change runId to id if it exists
        productRunId = provenance.pop("productRunId", None)
        if productRunId:
            provenance["id"] = productRunId
        # Else if no productRunId, then one will be assigned by the database

    url = f"{HOSTNAME}/{path}"

    # Restructure the object and remove unneeded fields
    provenance_copy = provenance.copy()
    provenance_copy.pop("sourceUrl", None)
    provenance_copy.pop("sourceMetadataUrl", None)
    provenance_copy.pop("provenanceUpdated", None)
    provenance_copy["provenanceJson"] = provenance

    return _post(url, provenance_copy)


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


def post_product_output_bulk(bulk_product_output: dict) -> Response:
    # Check for API key
    _check_api_key()

    url = f"{HOSTNAME}/api/v0/product-output/bulk"

    return _post(url, bulk_product_output)
