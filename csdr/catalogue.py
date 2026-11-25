"""Read-only catalogue reader for checks that bind a calculation to app records.

``CatalogueReader`` reads single records
(``GET /api/v0/{product,dataset-run,geometries-run,geometry-output,
indicator/measured,product-run}/{id}``) and paginated lists such as
``geometries-run/{id}/outputs``.
Responses are capped in size and must identify the requested record. Requests
go through ``csdr.app_integration._get``, so they follow the same transport
rules as posts: organisation header and membership preflight, timeouts and no
redirects.

This verifies catalogue identity and declared inputs, not scientific approval.
It never writes to the catalogue and never records credentials.
"""

import hashlib
import json
import os
from urllib.parse import quote, urlsplit

import requests

from csdr import app_integration
from csdr.utils import CSDRException

MAX_RESPONSE_BYTES = 2 * 1024**2


def digest(value: dict) -> str:
    """SHA-256 of the canonical JSON of ``value``; refuses non-finite numbers."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class CatalogueReader:
    def __init__(
        self, hostname: str, api_key: str, organization_id: str | None = None
    ) -> None:
        url = urlsplit(hostname)
        if (
            url.scheme not in {"https", "http"}
            or (
                url.scheme == "http"
                and url.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise CSDRException(
                "Use the HTTPS API origin, or HTTP on loopback for local verification."
            )
        if not api_key or api_key.strip() != api_key:
            raise CSDRException("Set CSDR_API_KEY for the selected deployment.")
        self.hostname = hostname.rstrip("/")
        self._api_key = api_key
        self.organization_id = organization_id or None
        # Validate the organisation id before any request is sent.
        app_integration.organization_headers(self.organization_id)

    @classmethod
    def from_environment(cls) -> "CatalogueReader":
        """A reader for CSDR_API_HOSTNAME, CSDR_API_KEY and CSDR_API_ORGANIZATION_ID.

        The environment is read at call time, not import time.
        """
        return cls(
            os.environ.get("CSDR_API_HOSTNAME", ""),
            os.environ.get("CSDR_API_KEY", ""),
            os.environ.get("CSDR_API_ORGANIZATION_ID"),
        )

    def read(
        self, resource: str, identifier: str, *, missing_ok: bool = False
    ) -> dict | None:
        """One record. ``missing_ok`` turns HTTP 404 into ``None``."""
        if not isinstance(identifier, str) or not identifier or len(identifier) > 255:
            raise CSDRException("Supply a bounded catalogue identifier.")
        url = f"{self.hostname}/api/v0/{resource}/{quote(identifier, safe='')}"
        envelope = self._get(url, resource, missing_ok=missing_ok)
        if envelope is None:
            return None
        record = envelope.get("data")
        if (
            envelope.get("statusCode") != 200
            or not isinstance(record, dict)
            or record.get("id") != identifier
        ):
            raise CSDRException(
                "Catalogue response does not identify the requested record."
            )
        return record

    def list_records(
        self,
        resource: str,
        identifier: str,
        collection: str,
        *,
        page_size: int = 100,
        max_records: int = 100_000,
    ) -> list[dict]:
        """Read every record of a paginated list such as ``geometries-run/<id>/outputs``.

        Pages are ``data: {pageCount, totalCount, data: [...]}``, each bounded
        like a single read; the total is bounded by ``max_records``.
        """
        if not isinstance(identifier, str) or not identifier or len(identifier) > 255:
            raise CSDRException("Supply a bounded catalogue identifier.")
        base = (
            f"{self.hostname}/api/v0/{resource}/{quote(identifier, safe='')}/"
            f"{collection}?size={page_size}&sort=name&order=asc&page="
        )
        records: list[dict] = []
        page, page_count = 1, 1
        while page <= page_count:
            envelope = self._get(f"{base}{page}", f"{resource} {collection}")
            body = envelope.get("data")
            if (
                envelope.get("statusCode") != 200
                or not isinstance(body, dict)
                or not isinstance(body.get("data"), list)
                or type(body.get("pageCount")) is not int
                or type(body.get("totalCount")) is not int
                or not all(isinstance(item, dict) for item in body["data"])
            ):
                raise CSDRException("Catalogue list response is not a page of records.")
            if body["totalCount"] > max_records:
                raise CSDRException(
                    f"Catalogue list exceeds the {max_records} record limit."
                )
            page_count = body["pageCount"]
            records.extend(body["data"])
            page += 1
        if len(records) != body["totalCount"]:
            raise CSDRException(
                "Catalogue list changed while it was read. Retry the listing."
            )
        return records

    def _get(self, url: str, resource: str, *, missing_ok: bool = False) -> dict | None:
        try:
            status, raw = app_integration._get(
                url,
                hostname=self.hostname,
                api_key=self._api_key,
                organization_id=self.organization_id,
                limit_bytes=MAX_RESPONSE_BYTES,
            )
            if status == 404 and missing_ok:
                return None
            if status != 200:
                raise CSDRException(
                    f"Catalogue {resource} read failed (HTTP {status}). "
                    "Check deployment, credentials and record access. No output was written."
                )
            envelope = json.loads(raw)
            if not isinstance(envelope, dict):
                raise CSDRException(
                    "Catalogue response does not identify the requested record."
                )
            # Reject non-JSON numeric values before recording or comparing evidence.
            digest(envelope)
            return envelope
        except CSDRException:
            raise
        except (requests.RequestException, ValueError, TypeError):
            # Do not echo response bodies, headers or URLs containing credentials.
            raise CSDRException(
                "Could not read a valid catalogue record. No output was written."
            ) from None
