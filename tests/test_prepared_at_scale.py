"""Prepared values at scale: bind-regions templates and Parquet sources for prepare-precomputed."""

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import box
from typer.testing import CliRunner

from csdr import app_integration
from csdr.catalogue import CatalogueReader
from csdr.cli_products import products_app
from csdr.precomputed import (
    bind_regions,
    prepare_precomputed,
    read_parquet_source,
    validate_spec,
)
from csdr.products import parse_outputs
from csdr.utils import CSDRException

runner = CliRunner()

ROWS = [
    {"region_id": "region-101", "group": "x", "area_ha": 12.5, "count": 3, "note": "a"},
    {"region_id": "region-102", "group": "x", "area_ha": 0.0, "count": 0, "note": "b"},
    {"region_id": "region-103", "group": "x", "area_ha": None, "count": 7, "note": "c"},
]


def geometries(tmp_path: Path) -> Path:
    frame = gpd.GeoDataFrame(
        {
            "csdr-id": ["regions-one", "regions-two", "regions-three"],
            "csdr-name": ["One", "Two", "Three"],
            "code": [101, 102, 103],
        },
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(2, 0, 3, 1)],
        crs=4326,
    )
    path = tmp_path / "regions.parquet"
    frame.to_parquet(path)
    return path


def parquet_source(tmp_path: Path, rows: list[dict] = ROWS) -> Path:
    path = tmp_path / "source.parquet"
    table = pa.Table.from_pylist(rows).replace_schema_metadata(
        {"origin": "Synthetic prepared table"}
    )
    pq.write_table(table, path)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fill(template: dict, source: Path, columns: dict[str, str]) -> dict:
    """What a binding script does with a template: one binding per indicator."""
    spec = copy.deepcopy(template)
    spec.update(
        productId="p",
        productRunId="new-pr",
        datasetRunId="dr",
        sourceSha256=sha(source),
        role="context",
        method={"name": "test", "version": "1", "description": "Prepared externally."},
        reviewStatement="Synthetic fixture, not approved evidence.",
        caveats=["No allocation."],
        sourceProvenancePointers=["/schema/metadata/origin", "/rowCount"],
    )
    bindings = []
    for indicator, column in columns.items():
        for skeleton in template["bindings"]:
            binding = dict(skeleton)
            binding.update(
                indicatorId=indicator,
                timePoint="2026-08-24T00:00:00Z",
                unit="ha" if column == "area_ha" else "count",
                evidenceClass="observed",
                temporalBasis="Snapshot label, not an observation date.",
                valuePointer=skeleton["valuePointer"].replace("{{column}}", column),
            )
            bindings.append(binding)
    spec["bindings"] = bindings
    return spec


def test_offline_template_maps_keys_to_geometry_outputs(tmp_path: Path) -> None:
    template = bind_regions(
        "gr",
        key_column="code",
        key_template="region-{key}",
        offline_geometries_url=str(geometries(tmp_path)),
    )
    assert template["geometriesRunId"] == "gr"
    assert [
        (b["geometryOutputId"], b["valuePointer"]) for b in template["bindings"]
    ] == [
        ("regions-one", "/rows/region-101/{{column}}"),
        ("regions-two", "/rows/region-102/{{column}}"),
        ("regions-three", "/rows/region-103/{{column}}"),
    ]
    # The skeleton has the exact specification fields and is refused until filled.
    with pytest.raises(CSDRException, match="placeholder"):
        validate_spec(template)
    by_name = bind_regions(
        "gr", key_source="name", offline_geometries_url=str(geometries(tmp_path))
    )
    assert by_name["bindings"][0]["valuePointer"] == "/rows/One/{{column}}"
    by_id = bind_regions(
        "gr", key_source="csdr-id", offline_geometries_url=str(geometries(tmp_path))
    )
    assert {b["geometryOutputId"] for b in by_id["bindings"]} == {
        "regions-one",
        "regions-two",
        "regions-three",
    }


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"key_column": "missing"}, "cannot be bound"),
        ({"key_column": "code", "key_template": "region"}, "exactly once"),
        (
            {"key_column": "code", "key_source": "name"},
            "only with --key-source metadata",
        ),
        ({}, "key column"),
        ({"key_column": "code", "key_source": "label"}, "key source"),
        ({"key_column": "group"}, "also used by"),
    ],
)
def test_template_refusals(tmp_path: Path, kwargs: dict, message: str) -> None:
    path = geometries(tmp_path)
    frame = gpd.read_parquet(path)
    frame["group"] = "same"
    frame.to_parquet(path)
    with pytest.raises(CSDRException, match=message):
        bind_regions("gr", offline_geometries_url=str(path), **kwargs)


class ListingReader:
    def __init__(self) -> None:
        self.calls = []

    def read(self, resource: str, identifier: str, *, missing_ok: bool = False) -> dict:
        self.calls.append(("read", resource, identifier))
        return {"id": identifier}

    def list_records(
        self, resource: str, identifier: str, collection: str
    ) -> list[dict]:
        self.calls.append(("list", resource, identifier, collection))
        return [
            {
                "id": "out-1",
                "name": "One",
                "properties": {"code": "101"},
                "metadata": {"code": "stale"},
            },
            {"id": "out-2", "name": "Two", "metadata": {"code": 102}},
        ]


def test_catalogue_template_reads_the_run_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = ListingReader()
    monkeypatch.setattr(CatalogueReader, "from_environment", lambda: reader)
    out = tmp_path / "bindings-template.json"
    result = runner.invoke(
        products_app,
        [
            "bind-regions",
            "--geometries-run-id",
            "gr",
            "--key-column",
            "code",
            "--key-template",
            "region-{key}",
            "--out-file",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    template = json.loads(out.read_text())
    assert [
        (b["geometryOutputId"], b["valuePointer"]) for b in template["bindings"]
    ] == [
        ("out-1", "/rows/region-101/{{column}}"),  # properties win over metadata
        ("out-2", "/rows/region-102/{{column}}"),
    ]
    assert reader.calls == [
        ("read", "geometries-run", "gr"),
        ("list", "geometries-run", "gr", "outputs"),
    ]
    again = runner.invoke(
        products_app,
        [
            "bind-regions",
            "--geometries-run-id",
            "gr",
            "--key-column",
            "code",
            "--out-file",
            str(out),
        ],
    )
    assert isinstance(again.exception, CSDRException)


def test_paginated_listing_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        1: {
            "statusCode": 200,
            "data": {
                "pageCount": 2,
                "totalCount": 3,
                "data": [{"id": "a"}, {"id": "b"}],
            },
        },
        2: {
            "statusCode": 200,
            "data": {"pageCount": 2, "totalCount": 3, "data": [{"id": "c"}]},
        },
    }
    urls = []

    def get(url: str, **kwargs: object) -> MagicMock:
        urls.append(url)
        assert kwargs["allow_redirects"] is False
        context = MagicMock()
        response = context.__enter__.return_value
        response.status_code = 200
        page = int(url.rsplit("page=", 1)[1])
        response.iter_content.return_value = [json.dumps(pages[page]).encode()]
        return context

    monkeypatch.setattr(app_integration.requests, "get", get)
    reader = CatalogueReader("https://catalogue.example.test", "test-secret")
    assert [
        r["id"] for r in reader.list_records("geometries-run", "g/1", "outputs")
    ] == ["a", "b", "c"]
    assert urls[0].startswith(
        "https://catalogue.example.test/api/v0/geometries-run/g%2F1/outputs?size=100"
    )
    with pytest.raises(CSDRException, match="record limit"):
        reader.list_records("geometries-run", "g", "outputs", max_records=2)
    pages[2]["data"]["data"] = []
    with pytest.raises(CSDRException, match="changed while it was read"):
        reader.list_records("geometries-run", "g", "outputs")
    pages[1] = {"statusCode": 200, "data": {"rows": []}}
    with pytest.raises(CSDRException, match="page of records"):
        reader.list_records("geometries-run", "g", "outputs")


def test_parquet_bindings_equal_the_json_path(tmp_path: Path) -> None:
    source = parquet_source(tmp_path)
    json_source = tmp_path / "source.json"
    json_source.write_text(
        json.dumps({"rows": ROWS, "provenance": {"origin": "Synthetic prepared table"}})
    )

    def spec_for(path: Path, pointer: str, provenance: list[str]) -> Path:
        spec = {
            "version": 1,
            "productId": "p",
            "productRunId": "new-pr",
            "datasetRunId": "dr",
            "geometriesRunId": "gr",
            "sourceSha256": sha(path),
            "role": "context",
            "method": {
                "name": "test",
                "version": "1",
                "description": "Prepared externally.",
            },
            "reviewStatement": "Synthetic fixture.",
            "caveats": ["No allocation."],
            "sourceProvenancePointers": provenance,
            "bindings": [
                {
                    "geometryOutputId": f"g{i}",
                    "indicatorId": column,
                    "timePoint": "2026-08-24T00:00:00Z",
                    "valuePointer": pointer.format(
                        i=i, key=row["region_id"], column=column
                    ),
                    "unit": "u",
                    "evidenceClass": "observed",
                    "temporalBasis": "Snapshot.",
                }
                for i, row in enumerate(ROWS)
                for column in ("area_ha", "count")
            ],
        }
        spec_file = (
            tmp_path / f"spec-{path.suffix}-{len(list(tmp_path.iterdir()))}.json"
        )
        spec_file.write_text(json.dumps(spec))
        return spec_file

    from_json = prepare_precomputed(
        json_source,
        spec_for(json_source, "/rows/{i}/{column}", ["/provenance"]),
        tmp_path / "json",
        offline=True,
    )
    indexed = prepare_precomputed(
        source,
        spec_for(source, "/rows/{i}/{column}", ["/rowCount"]),
        tmp_path / "indexed",
        offline=True,
        source_format="parquet",
    )
    keyed = prepare_precomputed(
        source,
        spec_for(source, "/rows/{key}/{column}", ["/schema/metadata/origin"]),
        tmp_path / "keyed",
        offline=True,
        source_format="parquet",
        key_columns=["region_id"],
    )
    frames = {
        name: pd.read_parquet(tmp_path / name / "product.parquet")
        for name in ("json", "indexed", "keyed")
    }
    expected = {
        (row["geometryOutputId"], indicator): value
        for _, row in frames["json"].iterrows()
        for indicator, value in row["indicators"].items()
        if value is not None and not pd.isna(value)
    }
    for name in ("indexed", "keyed"):
        values = parse_outputs(frames[name])
        got = {
            (output["geometryOutputId"], indicator): output["value"]
            for indicator, by_time in values.items()
            for outputs in by_time.values()
            for output in outputs
        }
        assert got == expected
    assert expected[("g0", "area_ha")] == 12.5 and expected[("g1", "count")] == 0
    for receipt in (from_json, indexed, keyed):
        assert receipt["valueCount"] == 5
        assert len(receipt["nullBindings"]) == 1  # region-103 area_ha is null
    assert indexed["sourceProjection"]["pointerForm"] == "/rows/<index>/<column>"
    assert keyed["sourceProjection"]["pointerForm"] == "/rows/<region_id>/<column>"
    assert keyed["sourceProvenance"] == {
        "/schema/metadata/origin": "Synthetic prepared table"
    }
    assert indexed["sourceProvenance"] == {"/rowCount": 3}
    assert indexed["sourceSha256"] == sha(source)
    assert "sourceFormat" not in from_json  # JSON receipts are unchanged


def test_bind_regions_template_feeds_a_keyed_parquet_preparation(
    tmp_path: Path,
) -> None:
    source = parquet_source(tmp_path)
    template = bind_regions(
        "gr",
        key_column="code",
        key_template="region-{key}",
        offline_geometries_url=str(geometries(tmp_path)),
    )
    spec = fill(template, source, {"sum-x-area-ha": "area_ha", "count-x": "count"})
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec))
    receipt = prepare_precomputed(
        source,
        spec_file,
        tmp_path / "out",
        offline=True,
        source_format="parquet",
        key_columns=["region_id"],
    )
    assert receipt["valueCount"] == 5
    outputs = parse_outputs(pd.read_parquet(tmp_path / "out" / "product.parquet"))
    counts = {
        o["geometryOutputId"]: o["value"]
        for o in outputs["count-x"]["2026-08-24T00:00:00Z"]
    }
    assert counts == {"regions-one": 3, "regions-two": 0, "regions-three": 7}


def test_composite_keys_and_key_refusals(tmp_path: Path) -> None:
    rows = [
        {"region_id": "r1", "group": "parks", "v": 1.5},
        {"region_id": "r1", "group": "forest", "v": 2.5},
        {"region_id": "r2", "group": "parks", "v": 0.0},
    ]
    document, digest = read_parquet_source(
        parquet_source(tmp_path, rows), ["region_id", "group"]
    )
    assert document["rows"]["r1"]["forest"]["v"] == 2.5
    assert document["rows"]["r2"]["parks"]["v"] == 0.0
    assert document["rowCount"] == 3 and len(digest) == 64
    with pytest.raises(CSDRException, match="more than once"):
        read_parquet_source(parquet_source(tmp_path, rows), ["region_id"])
    with pytest.raises(CSDRException, match="non-blank strings or integers"):
        read_parquet_source(
            parquet_source(tmp_path, [{"region_id": None, "v": 1}]), ["region_id"]
        )
    with pytest.raises(CSDRException, match="distinct columns"):
        read_parquet_source(parquet_source(tmp_path, rows), ["missing"])
    integer_keys, _ = read_parquet_source(
        parquet_source(tmp_path, [{"code": 101, "v": 1}]), ["code"]
    )
    assert integer_keys["rows"]["101"]["v"] == 1
    with pytest.raises(CSDRException, match="Parquet sources only"):
        prepare_precomputed(
            tmp_path / "x.json",
            tmp_path / "s.json",
            tmp_path / "o",
            source_format="json",
            key_columns=["a"],
        )
    with pytest.raises(CSDRException, match="source format"):
        prepare_precomputed(
            tmp_path / "x.json",
            tmp_path / "s.json",
            tmp_path / "o",
            source_format="csv",
        )


def test_non_finite_parquet_values_are_refused_when_bound(tmp_path: Path) -> None:
    source = parquet_source(tmp_path, [{"region_id": "r1", "v": float("nan")}])
    template = {
        "version": 1,
        "productId": "p",
        "productRunId": "new-pr",
        "datasetRunId": "dr",
        "geometriesRunId": "gr",
        "sourceSha256": sha(source),
        "role": "context",
        "method": {"name": "t", "version": "1", "description": "d"},
        "reviewStatement": "r",
        "caveats": ["c"],
        "sourceProvenancePointers": ["/rowCount"],
        "bindings": [
            {
                "geometryOutputId": "g",
                "indicatorId": "i",
                "timePoint": "2026-08-24T00:00:00Z",
                "valuePointer": "/rows/r1/v",
                "unit": "u",
                "evidenceClass": "observed",
                "temporalBasis": "t",
            }
        ],
    }
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(template))
    with pytest.raises(CSDRException, match="finite"):
        prepare_precomputed(
            source,
            spec_file,
            tmp_path / "out",
            offline=True,
            source_format="parquet",
            key_columns=["region_id"],
        )
    template["sourceSha256"] = "0" * 64
    spec_file.write_text(json.dumps(template))
    with pytest.raises(CSDRException, match="pinned SHA-256"):
        prepare_precomputed(
            source,
            spec_file,
            tmp_path / "out2",
            offline=True,
            source_format="parquet",
            key_columns=["region_id"],
        )


def test_cli_prepares_a_parquet_source(tmp_path: Path) -> None:
    source = parquet_source(tmp_path)
    template = bind_regions(
        "gr",
        key_column="code",
        key_template="region-{key}",
        offline_geometries_url=str(geometries(tmp_path)),
    )
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(fill(template, source, {"count-x": "count"})))
    result = runner.invoke(
        products_app,
        [
            "prepare-precomputed",
            "--source",
            str(source),
            "--spec",
            str(spec_file),
            "--output",
            str(tmp_path / "out"),
            "--offline",
            "--source-format",
            "parquet",
            "--key-column",
            "region_id",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Prepared 3 values" in result.output


def test_verified_parquet_preparation_uses_the_declared_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_precomputed import Reader

    source = parquet_source(tmp_path)
    template = bind_regions(
        "gr",
        key_column="code",
        key_template="region-{key}",
        offline_geometries_url=str(geometries(tmp_path)),
    )
    spec = fill(template, source, {"i0": "count"})
    for binding in spec["bindings"]:
        binding["unit"] = "AUD"
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec))
    reader = Reader(spec)
    for output_id in ("regions-one", "regions-two", "regions-three"):
        reader.records[("geometry-output", output_id)] = {
            "id": output_id,
            "geometriesRun": {"id": "gr"},
        }
    monkeypatch.setattr(
        "csdr.precomputed.CatalogueReader.from_environment", lambda: reader
    )
    receipt = prepare_precomputed(
        source,
        spec_file,
        tmp_path / "out",
        source_format="parquet",
        key_columns=["region_id"],
    )
    assert receipt["catalogueVerification"]["status"] == "catalogue-inputs-matched"


def test_only_template_placeholders_are_refused(tmp_path: Path) -> None:
    source = parquet_source(tmp_path)
    template = bind_regions(
        "gr",
        key_column="code",
        key_template="region-{key}",
        offline_geometries_url=str(geometries(tmp_path)),
    )
    spec = fill(template, source, {"count-x": "count"})
    # Free text may contain braces, e.g. a workflow-engine expression.
    spec["reviewStatement"] = "Prepared by {{workflow.name}}; not approved evidence."
    spec["caveats"] = ["Values as published by {{source.owner}}."]
    validate_spec(spec)
    left = copy.deepcopy(spec)
    left["bindings"][0]["valuePointer"] = template["bindings"][0]["valuePointer"]
    with pytest.raises(CSDRException, match="placeholder"):
        validate_spec(left)
    left = copy.deepcopy(spec)
    left["bindings"][1]["unit"] = "{{unit}}"
    with pytest.raises(CSDRException, match="placeholder"):
        validate_spec(left)
    left = copy.deepcopy(spec)
    left["reviewStatement"] = "{{reviewStatement}}"
    with pytest.raises(CSDRException, match="placeholder"):
        validate_spec(left)
