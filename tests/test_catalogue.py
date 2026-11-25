"""Read-only catalogue reader: origin checks, transport rules, bounds and organisation."""

from unittest.mock import MagicMock

import pytest

from csdr import app_integration
from csdr.catalogue import MAX_RESPONSE_BYTES, CatalogueReader, digest
from csdr.utils import CSDRException


@pytest.fixture(autouse=True)
def isolated_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh membership cache and no inherited API configuration."""
    monkeypatch.setattr(app_integration, "_verified_memberships", set())
    for name in (
        "CSDR_API_ORGANIZATION_ID",
        "CSDR_API_CONNECT_TIMEOUT",
        "CSDR_API_READ_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://remote.test",
        "https://user:secret@remote.test",
        "https://remote.test/api",
        "https://remote.test/?secret=abc",
    ],
)
def test_unsafe_origins_are_rejected(url: str) -> None:
    with pytest.raises(CSDRException, match="API origin"):
        CatalogueReader(url, "test-key")


def test_http_contract_bounds_and_credential_safety(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock()
    response = get.return_value.__enter__.return_value
    response.status_code = 200
    response.iter_content.return_value = [b'{"statusCode":200,"data":{"id":"x/y"}}']
    monkeypatch.setattr(app_integration.requests, "get", get)
    reader = CatalogueReader("https://catalogue.example.test", "test-secret")
    assert reader.read("product", "x/y") == {"id": "x/y"}
    assert get.call_args.args[0].endswith("/product/x%2Fy")
    assert get.call_args.kwargs["allow_redirects"] is False
    # The app transport rules of csdr.app_integration apply (#169).
    assert get.call_args.kwargs["timeout"] == app_integration.request_timeout()
    assert "x-sdf-active-organization-id" not in get.call_args.kwargs["headers"]
    for status in (302, 401, 403, 500):
        response.status_code = status
        with pytest.raises(CSDRException, match=f"HTTP {status}") as error:
            reader.read("product", "x", missing_ok=True)
        assert "test-secret" not in str(error.value)
    response.status_code = 404
    assert reader.read("product-run", "fresh", missing_ok=True) is None
    response.status_code = 200
    response.iter_content.return_value = [b"x" * (MAX_RESPONSE_BYTES + 1)]
    with pytest.raises(CSDRException, match="2 MiB"):
        reader.read("product", "x")
    response.iter_content.return_value = [b'{"statusCode":200,"data":{"id":"other"}}']
    with pytest.raises(CSDRException, match="requested record"):
        reader.read("product", "x")
    response.iter_content.return_value = [b'{"statusCode":200,"data":{"id":NaN}}']
    with pytest.raises(CSDRException, match="valid catalogue record"):
        reader.read("product", "x")


def test_reader_uses_same_explicit_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CSDR_API_HOSTNAME", "https://catalogue.example.test")
    monkeypatch.setenv("CSDR_API_KEY", "test-secret")
    monkeypatch.setenv("CSDR_API_ORGANIZATION_ID", "target-organization")
    get = MagicMock()
    membership = MagicMock()
    membership.__enter__.return_value.status_code = 200
    membership.__enter__.return_value.iter_content.return_value = [
        b'[{"id":"target-organization"}]'
    ]
    record = MagicMock()
    record.__enter__.return_value.status_code = 200
    record.__enter__.return_value.iter_content.return_value = [
        b'{"statusCode":200,"data":{"id":"p"}}'
    ]
    get.side_effect = [membership, record]
    monkeypatch.setattr(app_integration.requests, "get", get)
    reader = CatalogueReader.from_environment()
    assert reader.read("product", "p") == {"id": "p"}
    assert reader.organization_id == "target-organization"
    assert all(
        call.kwargs["headers"]["x-sdf-active-organization-id"] == "target-organization"
        for call in get.call_args_list
    )


def test_reader_refuses_a_foreign_organization_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = MagicMock()
    response = get.return_value.__enter__.return_value
    response.status_code = 200
    response.iter_content.return_value = [b'[{"id":"personal"}]']
    monkeypatch.setattr(app_integration.requests, "get", get)
    reader = CatalogueReader("https://catalogue.example.test", "test-secret", "typo")
    with pytest.raises(CSDRException, match="not a member"):
        reader.read("product", "p")
    assert get.call_count == 1  # the membership preflight only


@pytest.mark.parametrize("value", [" organization", "a\r\nx-injected:yes", "x" * 256])
def test_invalid_organization_id_is_refused_up_front(value: str) -> None:
    with pytest.raises(CSDRException, match="valid organisation ID"):
        CatalogueReader("https://catalogue.example.test", "test-secret", value)


def test_digest_is_canonical_and_refuses_non_finite_numbers() -> None:
    assert digest({"b": 1, "a": [1, 2]}) == digest({"a": [1, 2], "b": 1})
    with pytest.raises(ValueError):
        digest({"a": float("nan")})
