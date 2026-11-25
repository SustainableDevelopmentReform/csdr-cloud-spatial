import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from csdr.precomputed import (
    prepare_precomputed,
    project_values,
    read_json,
    verify_catalogue,
)
from csdr.products import parse_outputs
from csdr.utils import CSDRException


def fixture(tmp_path: Path) -> tuple[dict, dict, Path, Path]:
    document = {
        "provenance": {"origin": "Synthetic test source"},
        "values": [12.3456789012345, 0, None],
    }
    source = tmp_path / "source.json"
    source.write_text(json.dumps(document))
    spec = {
        "version": 1,
        "productId": "p",
        "productRunId": "new-pr",
        "datasetRunId": "dr",
        "geometriesRunId": "gr",
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "role": "context",
        "method": {
            "name": "test",
            "version": "1",
            "description": "Pre-computed test values",
        },
        "reviewStatement": "Test fixture, not approved evidence.",
        "caveats": ["No regional allocation."],
        "sourceProvenancePointers": ["/provenance"],
        "bindings": [
            {
                "geometryOutputId": "g",
                "indicatorId": f"i{i}",
                "timePoint": "2026-10-17T04:16:37Z",
                "valuePointer": f"/values/{i}",
                "unit": "AUD",
                "evidenceClass": "linked",
                "temporalBasis": "Snapshot of whole-life commitments, not annual expenditure.",
            }
            for i in range(3)
        ],
    }
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec))
    return document, spec, source, spec_file


class Reader:
    organization_id = "org"

    def __init__(self, spec: dict) -> None:
        self.records = {
            ("product", "p"): {
                "id": "p",
                "organizationId": "org",
                "dataset": {"id": "d"},
                "geometries": {"id": "gg"},
            },
            ("dataset-run", "dr"): {
                "id": "dr",
                "dataset": {"id": "d"},
                "provenanceJson": {
                    "precomputedSource": {
                        "version": 1,
                        "sourceSha256": spec["sourceSha256"],
                        "role": "context",
                    },
                },
            },
            ("geometries-run", "gr"): {"id": "gr", "geometries": {"id": "gg"}},
            ("geometry-output", "g"): {"id": "g", "geometriesRun": {"id": "gr"}},
            **{
                ("indicator/measured", f"i{i}"): {"id": f"i{i}", "unit": "AUD"}
                for i in range(3)
            },
        }

    def read(
        self, resource: str, identifier: str, *, missing_ok: bool = False
    ) -> dict | None:
        if missing_ok:
            return self.records.get((resource, identifier))
        return self.records[(resource, identifier)]


def test_exact_values_null_receipt_and_existing_product_format(tmp_path: Path) -> None:
    document, spec, source, spec_file = fixture(tmp_path)
    out = tmp_path / "prepared"
    receipt = prepare_precomputed(source, spec_file, out, offline=True)
    assert receipt["valueCount"] == 2
    assert receipt["nullBindings"] == [spec["bindings"][2]]
    assert receipt["catalogueVerification"]["status"] == "not-verified"
    assert receipt["sourceProvenance"] == {"/provenance": document["provenance"]}
    frame = pd.read_parquet(out / "product.parquet")
    assert frame.iloc[0]["indicators"] == {"i0": 12.3456789012345, "i1": 0}
    outputs = parse_outputs(frame)
    assert outputs["i0"]["2026-10-17T04:16:37Z"][0]["value"] == 12.3456789012345
    with pytest.raises(CSDRException, match="new output directory"):
        prepare_precomputed(source, spec_file, out, offline=True)
    assert source.read_text() == json.dumps(document)


def test_sparse_parquet_fields_never_become_values(tmp_path: Path) -> None:
    _, spec, source, spec_file = fixture(tmp_path)
    spec["bindings"][1]["geometryOutputId"] = "other-region"
    spec_file.write_text(json.dumps(spec))
    prepare_precomputed(source, spec_file, tmp_path / "prepared", offline=True)
    outputs = parse_outputs(pd.read_parquet(tmp_path / "prepared" / "product.parquet"))
    assert len(outputs["i0"]["2026-10-17T04:16:37Z"]) == 1
    assert outputs["i1"]["2026-10-17T04:16:37Z"][0]["value"] == 0
    assert (
        outputs["i1"]["2026-10-17T04:16:37Z"][0]["geometryOutputId"] == "other-region"
    )
    assert "i2" not in outputs


@pytest.mark.parametrize(
    "value", [True, "12.3", float("inf"), float("nan"), 2**53, 10**400]
)
def test_invalid_numeric_values_do_not_get_coerced(
    tmp_path: Path, value: object
) -> None:
    document, spec, _, _ = fixture(tmp_path)
    document["values"][0] = value
    with pytest.raises(CSDRException, match="finite JSON numbers"):
        project_values(document, spec)


def test_missing_duplicate_and_escaped_paths(tmp_path: Path) -> None:
    document, spec, _, _ = fixture(tmp_path)
    spec["bindings"][0]["valuePointer"] = "/missing"
    with pytest.raises(CSDRException, match="Missing data"):
        project_values(document, spec)
    document["a/b"] = {"~value": 64}
    spec["bindings"][0]["valuePointer"] = "/a~1b/~0value"
    assert project_values(document, spec)[0][0]["indicators"]["i0"] == 64
    spec["bindings"].append(copy.deepcopy(spec["bindings"][0]))
    spec["bindings"][-1]["timePoint"] = "2026-10-17T04:16:37.000Z"
    with pytest.raises(CSDRException, match="Duplicate geometry"):
        project_values(document, spec)


def test_source_identity_and_ambiguous_json_fail_before_output(tmp_path: Path) -> None:
    _, _, source, spec_file = fixture(tmp_path)
    out = tmp_path / "prepared"
    source.write_text('{"values": [9]}')
    with pytest.raises(CSDRException, match="pinned SHA-256"):
        prepare_precomputed(source, spec_file, out, offline=True)
    assert not out.exists()
    source.write_text('{"a": 1, "a": 2}')
    with pytest.raises(CSDRException, match="Duplicate JSON"):
        read_json(source)


@pytest.mark.parametrize(
    "change,message",
    [
        (
            lambda r: r.records[("product", "p")].update(organizationId="elsewhere"),
            "organisation",
        ),
        (
            lambda r: r.records[("product", "p")].update(dataset={"id": "other"}),
            "catalogue chain",
        ),
        (
            lambda r: r.records[("geometry-output", "g")].update(
                geometriesRun={"id": "other"}
            ),
            "boundary run",
        ),
        (
            lambda r: r.records[("indicator/measured", "i0")].update(unit="ha"),
            "unit differs",
        ),
        (
            lambda r: r.records[("dataset-run", "dr")].update(
                metadata={"preparedViewAccess": {"version": 1, "status": "withheld"}}
            ),
            "withheld",
        ),
        (
            lambda r: r.records[("dataset-run", "dr")]["provenanceJson"][
                "precomputedSource"
            ].update(sourceSha256="0" * 64),
            "same version 1",
        ),
        (
            lambda r: r.records.update({("product-run", "new-pr"): {"id": "new-pr"}}),
            "fresh product run",
        ),
    ],
)
def test_catalogue_mismatch_is_rejected(
    tmp_path: Path, change: object, message: str
) -> None:
    _, spec, _, _ = fixture(tmp_path)
    reader = Reader(spec)
    assert verify_catalogue(spec, reader)["status"] == "catalogue-inputs-matched"
    change(reader)
    with pytest.raises(CSDRException, match=message):
        verify_catalogue(spec, reader)


def test_concurrent_catalogue_change_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, spec, source, spec_file = fixture(tmp_path)
    reader = Reader(spec)
    monkeypatch.setattr(
        "csdr.precomputed.CatalogueReader.from_environment", lambda: reader
    )
    original_read = reader.read
    calls = 0

    def read(
        resource: str, identifier: str, *, missing_ok: bool = False
    ) -> dict | None:
        nonlocal calls
        if resource == "product":
            calls += 1
            if calls == 2:
                reader.records[("product", "p")]["updatedAt"] = "2026-11-17T12:00:00Z"
        return original_read(resource, identifier, missing_ok=missing_ok)

    reader.read = read
    out = tmp_path / "prepared"
    with pytest.raises(CSDRException, match="changed during preparation"):
        prepare_precomputed(source, spec_file, out)
    assert not out.exists()
