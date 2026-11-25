"""Provenance passthrough: --source-sha256, --source-version-id, --provenance-extra."""

import hashlib
import json
from unittest.mock import MagicMock
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box
from typer.testing import CliRunner

from csdr import app_integration
from csdr.cli_provenance import provenance_app
from csdr.provenance import RESERVED_PROVENANCE_KEYS
from csdr.utils import CSDRException

runner = CliRunner()
SHA = "ab" * 32


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CSDR_PROVENANCE_STEPS_DIR", str(tmp_path / "steps"))
    monkeypatch.setenv("IMAGE_REPO", "https://github.com/example/toolkit/tree/c/")
    monkeypatch.setenv("IMAGE_TAG", "fixture")


@pytest.fixture
def data_file(tmp_path: Path) -> Path:
    path = tmp_path / "data.parquet"
    gpd.GeoDataFrame(
        {"csdr-id": ["g"], "csdr-name": ["G"]}, geometry=[box(0, 0, 1, 1)], crs=4326
    ).to_parquet(path)
    return path


def invoke(*args: str) -> None:
    result = runner.invoke(provenance_app, list(args))
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output


def dataset_args(data_file: Path) -> list[str]:
    return [
        "dataset",
        "--id",
        "d",
        "--dataset-url",
        str(data_file),
        "--dataset-type",
        "geoparquet",
        "--source-metadata-url",
        "https://example.test/metadata",
        "--workflow-dag",
        "[]",
    ]


def geometry_args(data_file: Path) -> list[str]:
    return [
        "geometry",
        "--id",
        "g",
        "--run-id",
        "gr",
        "--geometry-url",
        str(data_file),
        "--geometry-type",
        "geoparquet",
        "--source-metadata-url",
        "https://example.test/metadata",
        "--workflow-dag",
        "[]",
    ]


def written(data_file: Path) -> dict:
    return json.loads(Path(f"{data_file}.provenance.json").read_text())


@pytest.mark.parametrize("args", [dataset_args, geometry_args])
def test_source_identity_is_written_only_when_given(
    data_file: Path, args: object
) -> None:
    invoke(*args(data_file))
    provenance = written(data_file)
    assert "sourceSha256" not in provenance
    assert "sourceVersionId" not in provenance

    invoke(
        *args(data_file),
        "--overwrite",
        "--source-sha256",
        SHA.upper(),
        "--source-version-id",
        "v-2026-11",
        "--provenance-extra",
        json.dumps({"sourceLicence": "CC-BY-4.0", "release": {"edition": 3}}),
    )
    provenance = written(data_file)
    assert provenance["sourceSha256"] == SHA
    assert provenance["sourceVersionId"] == "v-2026-11"
    assert provenance["sourceLicence"] == "CC-BY-4.0"
    assert provenance["release"] == {"edition": 3}
    assert provenance["dataUrl"] == str(data_file)


def test_product_accepts_provenance_extra(tmp_path: Path) -> None:
    product = tmp_path / "product.parquet"
    pd.DataFrame(
        [
            {
                "geometryOutputId": "g",
                "timePoint": "2024-01-01T00:00:00Z",
                "indicators": {"sum-x-area": 1.0},
            }
        ]
    ).to_parquet(product)
    invoke(
        "product",
        "--product-url",
        str(product),
        "--product-id",
        "p",
        "--dataset-run-id",
        "dr",
        "--geometries-run-id",
        "gr",
        "--workflow-dag",
        "[]",
        "--provenance-extra",
        '{"methodNote": "synthetic"}',
    )
    provenance = written(product)
    assert provenance["methodNote"] == "synthetic"
    assert provenance["datasetRunId"] == "dr"


@pytest.mark.parametrize(
    "key", ["dataUrl", "imageCode", "imageTag", "workflowDag", "dataEtag", "id"]
)
def test_reserved_keys_are_refused_before_anything_is_written(
    data_file: Path, key: str
) -> None:
    assert key in RESERVED_PROVENANCE_KEYS
    with pytest.raises(CSDRException, match=f"reserved provenance keys: \\['{key}'\\]"):
        invoke(*dataset_args(data_file), "--provenance-extra", json.dumps({key: "x"}))
    assert not Path(f"{data_file}.provenance.json").exists()


@pytest.mark.parametrize(
    "option,value,message",
    [
        ("--provenance-extra", "[1, 2]", "must be a JSON object"),
        ("--provenance-extra", "{oops", "not valid JSON"),
        ("--provenance-extra", '{"sourceSha256": "x"}', "reserved provenance keys"),
        ("--source-sha256", "sha256:abc", "64 hexadecimal"),
        ("--source-version-id", " ", "must not be blank"),
    ],
)
def test_invalid_passthrough_values_are_refused(
    data_file: Path, option: str, value: str, message: str
) -> None:
    with pytest.raises(CSDRException, match=message):
        invoke(*dataset_args(data_file), option, value)


def test_declarations_go_to_provenance_json_not_the_run_body(
    data_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "values.json"
    source.write_text(json.dumps({"values": [12.5]}))
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    declaration = {"version": 1, "sourceSha256": source_sha, "role": "context"}

    monkeypatch.setattr(app_integration, "API_KEY", "test-key")
    monkeypatch.setattr(app_integration, "HOSTNAME", "https://app.example.test")
    monkeypatch.delenv("CSDR_API_ORGANIZATION_ID", raising=False)
    post = MagicMock()
    post.return_value.status_code = 201
    post.return_value.json.return_value = {"data": {"id": "dr"}}
    monkeypatch.setattr(app_integration.requests, "post", post)
    invoke(
        *dataset_args(data_file),
        "--post-to-database",
        "--source-sha256",
        source_sha,
        "--source-version-id",
        "v1",
        "--provenance-extra",
        json.dumps({"exampleDeclaration": declaration}),
    )
    url = post.call_args.args[0]
    body = post.call_args.kwargs["json"]
    assert url == "https://app.example.test/api/v0/dataset-run"
    # Declarations live in provenanceJson (and the file), not the strict run body.
    for key in ("exampleDeclaration", "sourceSha256", "sourceVersionId"):
        assert key not in body
    assert body["provenanceJson"]["exampleDeclaration"] == declaration
    assert body["provenanceJson"]["sourceSha256"] == source_sha
    assert body["provenanceJson"]["sourceVersionId"] == "v1"
    assert body["datasetId"] == "d"
    assert written(data_file)["exampleDeclaration"] == declaration
