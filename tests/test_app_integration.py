import json
from unittest.mock import MagicMock

import pytest

from csdr import app_integration
from csdr.app_integration import (
    ORGANIZATION_HEADER,
    check_organization_membership,
    organization_headers,
    request_timeout,
)
from csdr.utils import CSDRException

HOSTNAME = "https://app.example.test"


@pytest.fixture(autouse=True)
def isolated_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh API configuration and membership cache for every test."""
    monkeypatch.setattr(app_integration, "HOSTNAME", HOSTNAME)
    monkeypatch.setattr(app_integration, "API_KEY", "test-secret")
    monkeypatch.setattr(app_integration, "_verified_memberships", set())
    for name in (
        "CSDR_API_ORGANIZATION_ID",
        "CSDR_API_CONNECT_TIMEOUT",
        "CSDR_API_READ_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


def mocked_memberships(
    monkeypatch: pytest.MonkeyPatch, records: list[dict[str, str]]
) -> tuple[MagicMock, MagicMock]:
    get = MagicMock()
    response = get.return_value.__enter__.return_value
    response.status_code = 200
    response.iter_content.return_value = [json.dumps(records).encode()]
    monkeypatch.setattr(app_integration.requests, "get", get)
    return get, response


def mocked_post(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    post = MagicMock()
    post.return_value.status_code = 201
    monkeypatch.setattr(app_integration.requests, "post", post)
    return post


def test_post_without_organization_sends_no_header_and_no_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get, _ = mocked_memberships(monkeypatch, [])
    post = mocked_post(monkeypatch)

    app_integration.post_geometry_output({"id": "g"})

    get.assert_not_called()
    kwargs = post.call_args.kwargs
    assert ORGANIZATION_HEADER not in kwargs["headers"]
    assert kwargs["headers"]["x-api-key"] == "test-secret"
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (10.0, 60.0)


def test_post_with_organization_sends_header_and_checks_membership_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CSDR_API_ORGANIZATION_ID", "target-org")
    get, _ = mocked_memberships(monkeypatch, [{"id": "personal"}, {"id": "target-org"}])
    post = mocked_post(monkeypatch)

    for _ in range(3):
        app_integration.post_product_output_bulk({"outputs": []})

    # The membership check is cached per process, not repeated per POST.
    assert get.call_count == 1
    assert get.call_args.kwargs["allow_redirects"] is False
    assert get.call_args.kwargs["headers"][ORGANIZATION_HEADER] == "target-org"
    assert post.call_count == 3
    assert all(
        call.kwargs["headers"][ORGANIZATION_HEADER] == "target-org"
        for call in post.call_args_list
    )


def test_membership_is_checked_again_for_another_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get, _ = mocked_memberships(monkeypatch, [{"id": "org-a"}, {"id": "org-b"}])
    check_organization_membership(HOSTNAME, "test-secret", "org-a")
    check_organization_membership(HOSTNAME, "test-secret", "org-a")
    check_organization_membership(HOSTNAME, "test-secret", "org-b")
    assert get.call_count == 2


def test_wrong_organization_never_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CSDR_API_ORGANIZATION_ID", "typo")
    get, response = mocked_memberships(monkeypatch, [{"id": "personal"}])
    post = mocked_post(monkeypatch)

    with pytest.raises(CSDRException, match="not a member"):
        app_integration.post_product_output_bulk({"outputs": []})
    post.assert_not_called()

    # A failed check is not cached: the next call checks again.
    response.status_code = 403
    with pytest.raises(CSDRException, match="Could not verify"):
        app_integration.post_product_output_bulk({"outputs": []})
    post.assert_not_called()
    assert get.call_count == 2


def test_redirect_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    post = mocked_post(monkeypatch)
    post.return_value.status_code = 307
    with pytest.raises(CSDRException, match="redirected"):
        app_integration.post_geometry_output({"id": "g"})


def test_timeouts_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    assert request_timeout() == (10.0, 60.0)
    monkeypatch.setenv("CSDR_API_CONNECT_TIMEOUT", "5")
    monkeypatch.setenv("CSDR_API_READ_TIMEOUT", "300")
    assert request_timeout() == (5.0, 300.0)

    post = mocked_post(monkeypatch)
    app_integration.post_geometry_output({"id": "g"})
    assert post.call_args.kwargs["timeout"] == (5.0, 300.0)


@pytest.mark.parametrize("value", ["abc", "0", "-1"])
def test_invalid_timeouts_are_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CSDR_API_READ_TIMEOUT", value)
    with pytest.raises(CSDRException, match="CSDR_API_READ_TIMEOUT"):
        request_timeout()


@pytest.mark.parametrize(
    "value", [" org", "org\r\nx-injected:yes", "x" * 256, "wrong\u0000"]
)
def test_organization_header_validation(value: str) -> None:
    with pytest.raises(CSDRException, match="organisation ID"):
        organization_headers(value)


def test_membership_response_is_bounded_and_does_not_echo_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, response = mocked_memberships(monkeypatch, [])
    response.iter_content.return_value = [b"x" * (2 * 1024**2 + 1)]
    with pytest.raises(CSDRException, match="2 MiB") as error:
        check_organization_membership(HOSTNAME, "test-secret", "target-org")
    assert "test-secret" not in str(error.value)


def test_post_provenance_keeps_workflow_template_fields_in_provenance_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict = {}
    monkeypatch.setattr(
        app_integration, "_post", lambda url, body: sent.update(body) or "ok"
    )
    template = {
        "workflowTemplateUrl": "s3://bucket/workflows/example.yaml",
        "workflowTemplateKind": "argo-workflowtemplate",
        "workflowTemplateName": "example",
        "workflowTemplateDigest": "sha256:" + "d" * 64,
    }
    provenance = {"id": "dataset-1", "dataUrl": "s3://bucket/x.parquet", **template}

    app_integration.post_provenance(provenance, type="dataset")

    assert not set(template) & set(sent)
    assert {key: sent["provenanceJson"][key] for key in template} == template
