import json
import logging
import os
from collections.abc import Iterable
from typing import Literal

import requests
from requests import Response

from csdr.provenance import PROVENANCE_JSON_ONLY_KEYS, WORKFLOW_TEMPLATE_FIELDS
from csdr.utils import CSDRException

logger = logging.getLogger(__name__)

HOSTNAME = os.getenv("CSDR_API_HOSTNAME", "http://localhost:4000").rstrip("/")
API_KEY = os.getenv("CSDR_API_KEY", None)

ALLOWED_TYPES = ["dataset", "geometry", "product"]

# Header the app uses to select the active organisation for API keys whose owner
# belongs to several organisations.
ORGANIZATION_HEADER = "x-sdf-active-organization-id"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
DEFAULT_READ_TIMEOUT_SECONDS = 60.0
MEMBERSHIP_RESPONSE_LIMIT_BYTES = 2 * 1024**2

# (hostname, api key, organisation id) combinations already verified by this process.
_verified_memberships: set[tuple[str, str, str]] = set()


def _check_api_key() -> None:
    if API_KEY is None:
        raise CSDRException(
            "API key must be provided in CSDR_API_KEY environment variable"
        )


def _timeout_from_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        seconds = float(value)
    except ValueError:
        raise CSDRException(f"{name} must be a number of seconds.") from None
    if seconds <= 0:
        raise CSDRException(f"{name} must be greater than zero.")
    return seconds


def request_timeout() -> tuple[float, float]:
    """(connect, read) timeout in seconds for app API requests.

    Defaults to (10, 60). Override with CSDR_API_CONNECT_TIMEOUT and
    CSDR_API_READ_TIMEOUT, for example for large bulk posts.
    """
    return (
        _timeout_from_env("CSDR_API_CONNECT_TIMEOUT", DEFAULT_CONNECT_TIMEOUT_SECONDS),
        _timeout_from_env("CSDR_API_READ_TIMEOUT", DEFAULT_READ_TIMEOUT_SECONDS),
    )


def organization_headers(organization_id: str | None) -> dict[str, str]:
    """The active-organisation header, or nothing when no organisation is selected."""
    if organization_id is None or organization_id == "":
        return {}
    if len(organization_id) > 255 or any(
        ord(char) < 33 or ord(char) > 126 for char in organization_id
    ):
        raise CSDRException("CSDR_API_ORGANIZATION_ID must be a valid organisation ID.")
    return {ORGANIZATION_HEADER: organization_id}


def check_organization_membership(
    hostname: str, api_key: str, organization_id: str | None
) -> None:
    """Refuse an organisation the API key's owner is not a member of.

    Without this check the app falls back to the owner's persisted or first
    organisation, so a typo would post records into the wrong organisation.
    This is a preflight, not a replacement for server-side authorisation.
    The result is cached per process: one GET per (hostname, key, organisation),
    not one per POST. Calls without an organisation make no extra request.
    """
    headers = organization_headers(organization_id)
    if not headers:
        return
    cache_key = (hostname, api_key, organization_id)
    if cache_key in _verified_memberships:
        return
    try:
        with requests.get(
            f"{hostname.rstrip('/')}/api/auth/organization/list",
            headers={"x-api-key": api_key, "Accept": "application/json", **headers},
            allow_redirects=False,
            timeout=request_timeout(),
            stream=True,
        ) as response:
            if response.status_code != 200:
                raise CSDRException(
                    "Could not verify membership of CSDR_API_ORGANIZATION_ID."
                )
            raw = bytearray()
            for chunk in response.iter_content(64 * 1024):
                raw.extend(chunk)
                if len(raw) > MEMBERSHIP_RESPONSE_LIMIT_BYTES:
                    raise CSDRException(
                        "Organisation membership response exceeds 2 MiB."
                    )
            records = json.loads(raw)
    except (requests.RequestException, ValueError, TypeError):
        raise CSDRException(
            "Could not verify membership of CSDR_API_ORGANIZATION_ID."
        ) from None
    if not isinstance(records, list) or not any(
        isinstance(record, dict) and record.get("id") == organization_id
        for record in records
    ):
        raise CSDRException(
            "The API key's owner is not a member of CSDR_API_ORGANIZATION_ID. "
            "No request was sent."
        )
    _verified_memberships.add(cache_key)


def _post(url: str, json: dict) -> Response:
    _check_api_key()
    organization_id = os.getenv("CSDR_API_ORGANIZATION_ID")
    check_organization_membership(HOSTNAME, API_KEY, organization_id)

    headers = {
        "x-api-key": API_KEY,
        "Content-Type": "application/json",
        **organization_headers(organization_id),
    }

    response = requests.post(
        url,
        json=json,
        headers=headers,
        allow_redirects=False,
        timeout=request_timeout(),
    )
    if 300 <= response.status_code < 400:
        # Following a redirect would resend the API key to another origin.
        raise CSDRException(
            f"The app API redirected the request (HTTP {response.status_code}). "
            "Check CSDR_API_HOSTNAME."
        )
    return response


def post_provenance(
    provenance: dict[str, str | int | dict[str, str | int]],
    type: Literal["dataset", "geometry", "product"],
    json_only_keys: Iterable[str] = (),
) -> Response:
    """Post a run. ``json_only_keys`` stay in ``provenanceJson`` only."""
    # Check for API key
    _check_api_key()

    if type not in ALLOWED_TYPES:
        raise CSDRException(f"Type must be one of {ALLOWED_TYPES}")

    # IDs are reassigned based on type so that the primary/foreign keys are correct for the database
    if type == "geometry":
        path = "api/v0/geometries-run"
        # We are writing a provenance for a geometry so the id is actually the geometry run id, and the geometry id needs to be stored separately.
        # Change id to geometryId
        provenance["geometriesId"] = provenance.pop(
            "id"
        )  # id is actually the geometry id.
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
    # Workflow template references, source identity and --provenance-extra
    # declarations are kept in provenanceJson only.
    for key in (*WORKFLOW_TEMPLATE_FIELDS, *PROVENANCE_JSON_ONLY_KEYS, *json_only_keys):
        provenance_copy.pop(key, None)
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
