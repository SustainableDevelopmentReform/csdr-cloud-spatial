"""products vector-aggregate: declared counting rules over a partitioned vector dataset.

Synthetic layers are constructed in the declared equal-area CRS around an offset
origin, written as partitioned EPSG:4326 GeoParquet (the geoparquet dataset
contract) and measured against boundaries of a geometries parquet.
"""

import copy
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from typer.testing import CliRunner

from csdr.catalogue import CatalogueReader
from csdr.cli_products import products_app
from csdr.io import get_store_with_prefix_from_url
from csdr.products import parse_outputs
from csdr.utils import CSDRException, partition_parquet
from csdr.vector_aggregate import validate_vector_aggregate_spec

runner = CliRunner()

ORIGIN = {"EPSG:3577": (1_000_000, -3_500_000), "EPSG:6933": (14_500_000, -4_000_000)}
BOUNDARY = box(0, 0, 200, 100)  # 20,000 m²


def ring(outer: tuple, hole: tuple) -> Polygon:
    return Polygon(box(*outer).exterior.coords, [box(*hole).exterior.coords])


# Features against BOUNDARY, with the relations each satisfies.
FEATURES = {
    # Area layer: two overlapping class-a squares (union 15,000 m², sum 20,000 m²)
    # and one class-b square crossing the boundary (2,500 m² inside).
    "a1": ("a", box(0, 0, 100, 100), 10.0, "alpha"),
    "a2": ("a", box(50, 0, 150, 100), None, "alpha"),
    "b1": ("b", box(150, 50, 250, 150), 100.0, "beta"),
    # Relation layer (class r).
    "inside1": ("r", box(20, 20, 40, 40), 1.0, "x"),
    "inside2": ("r", box(60, 20, 80, 40), 2.0, "y"),
    "touching": ("r", box(200, 0, 300, 100), 4.0, None),
    "mostly-out1": ("r", box(190, 40, 300, 60), 8.0, "x"),
    "mostly-out2": ("r", box(195, 70, 320, 80), 16.0, "z"),
    "mostly-in": ("r", box(150, 40, 210, 60), 32.0, "x"),
    "covering": ("r", box(-10, -10, 210, 110), 64.0, "y"),
    # Centroid (x = 264.7) outside, point on surface (185, 50) inside.
    "ell": (
        "r",
        Polygon([(180, 10), (400, 10), (400, 20), (190, 20), (190, 90), (180, 90)]),
        128.0,
        "x",
    ),
    # Rings around the boundary: centroid inside, no shared point.
    "ring1": ("r", ring((-100, -100, 300, 200), (-50, -50, 250, 150)), 256.0, "w"),
    "ring2": ("r", ring((-150, -150, 350, 250), (-120, -120, 320, 220)), 512.0, "w"),
    "far": ("r", box(5000, 5000, 5100, 5100), 1024.0, "far"),
}
RELATION_COUNTS = {
    "intersects": 8,  # inside x2, touching, mostly-out x2, mostly-in, covering, ell
    "within": 2,
    "contains": 2,  # alias of within
    "centroid-within": 6,  # inside x2, mostly-in, covering, ring x2
    "representative-point-within": 5,  # inside x2, mostly-in, covering, ell
    "covers-boundary": 1,
}


@pytest.fixture(autouse=True)
def steps_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "steps"
    monkeypatch.setenv("CSDR_PROVENANCE_STEPS_DIR", str(directory))
    monkeypatch.setenv(
        "IMAGE_REPO", "https://github.com/example/toolkit/tree/fixture-commit/"
    )
    return directory


def build(tmp_path: Path, crs: str = "EPSG:3577") -> dict[str, str]:
    """Write the partitioned dataset, three boundaries and both provenance files."""
    x0, y0 = ORIGIN[crs]

    def shift(geometry: BaseGeometry) -> BaseGeometry:
        return affinity.translate(geometry, x0, y0)

    names = list(FEATURES)
    features = gpd.GeoDataFrame(
        {
            "name": names,
            "kind": [FEATURES[n][0] for n in names],
            "credits": [FEATURES[n][2] for n in names],
            "species": [FEATURES[n][3] for n in names],
            "units": list(range(1, len(names) + 1)),
        },
        geometry=[shift(FEATURES[n][1]) for n in names],
        crs=crs,
    )
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    features.to_parquet(dataset / "features.parquet")
    partition_parquet(
        get_store_with_prefix_from_url(str(dataset)), features, file_prefix="features"
    )
    (dataset / "features.parquet.provenance.json").write_text(
        json.dumps(
            {"dataUrl": str(dataset / "features.parquet"), "dataType": "geoparquet"}
        )
    )
    boundaries = gpd.GeoDataFrame(
        {
            "csdr-id": ["main", "empty", "half"],
            "csdr-name": ["Main", "Empty", "Half"],
        },
        geometry=[
            shift(BOUNDARY),
            shift(box(3000, 3000, 3100, 3100)),
            shift(box(0, 0, 100, 100)),
        ],
        crs=crs,
    )
    boundaries.to_parquet(tmp_path / "boundaries.parquet")
    (tmp_path / "boundaries.parquet.provenance.json").write_text(
        json.dumps({"dataUrl": str(tmp_path / "boundaries.parquet")})
    )
    return {
        "dataset": str(dataset / "features.parquet.provenance.json"),
        "geometry": str(tmp_path / "boundaries.parquet.provenance.json"),
    }


def spec_for(measures: list[dict], crs: str = "EPSG:3577", **extra: object) -> dict:
    spec = {
        "version": 1,
        "productId": "p",
        "productRunId": "r",
        "datasetRunId": "dr",
        "geometriesRunId": "gr",
        "timePoint": "2024-06-30",
        "sourceDate": "2024-06-01",
        "areaCrs": crs,
        "evidenceClass": "observed_register",
        "caveat": "Synthetic layer for method verification.",
        "countingRule": "Features of each class counted by declared relation; areas clipped.",
        "classification": {"column": "kind", "values": ["a", "b", "r"]},
        "measures": measures,
    }
    spec.update(extra)
    return spec


def run(
    tmp_path: Path,
    inputs: dict[str, str],
    spec: dict,
    *ids: str,
    offline: bool = True,
    target: Path | None = None,
    extra: tuple[str, ...] = (),
) -> Path:
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec))
    target = target or tmp_path / "product"
    result = runner.invoke(
        products_app,
        [
            "vector-aggregate",
            "--spec",
            str(spec_file),
            "--run-id",
            spec["productRunId"],
            "--target-location",
            str(target),
            "--dataset-provenance-url",
            inputs["dataset"],
            "--geometry-provenance-url",
            inputs["geometry"],
            *(ids or ("--geometry-id", "main")),
            *(("--offline",) if offline else ()),
            *extra,
        ],
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output
    return target


def output(target: Path, directory: str, geometry_id: str = "main") -> dict:
    return json.loads(
        (target / directory / "2024-06-30" / f"p-{geometry_id}.json").read_text()
    )


def test_overlapping_features_are_unioned_or_summed_and_clipped(tmp_path: Path) -> None:
    inputs = build(tmp_path)
    measures = [
        {
            "indicatorId": "sum-a-area",
            "measure": "area-m2",
            "class": {"column": "kind", "values": ["a"]},
        },
        {
            "indicatorId": "sum-b-area",
            "measure": "area-m2",
            "class": {"column": "kind", "values": ["b"]},
        },
        {
            "indicatorId": "count-a",
            "measure": "count",
            "class": {"column": "kind", "values": ["a"]},
        },
    ]
    union = output(run(tmp_path, inputs, spec_for(measures)), "many-indicators")
    assert union["indicators"]["sum-a-area"] == pytest.approx(15_000, abs=0.5)
    assert union["indicators"]["sum-b-area"] == pytest.approx(2_500, abs=0.5)  # clipped
    assert union["indicators"]["count-a"] == 2
    summed = output(
        run(
            tmp_path,
            inputs,
            spec_for(measures, overlapPolicy="sum-features"),
            target=tmp_path / "summed",
        ),
        "many-indicators",
    )
    assert summed["indicators"]["sum-a-area"] == pytest.approx(20_000, abs=0.5)
    details = summed["metadata"]["vectorAggregate"]["measures"]["sum-a-area"]
    assert details["overlapPolicy"] == "sum-features"
    assert details["featureCount"] == 2
    assert union["metadata"]["vectorAggregate"]["overlapPolicy"] == "union"


@pytest.mark.parametrize("crs", ["EPSG:3577", "EPSG:6933"])
def test_both_equal_area_crs_are_accepted_and_area_ha_converts(
    tmp_path: Path, crs: str
) -> None:
    inputs = build(tmp_path, crs)
    measures = [
        {
            "indicatorId": "a-ha",
            "measure": "area-ha",
            "class": {"column": "kind", "values": ["a"]},
        },
        {
            "indicatorId": "a-m2",
            "measure": "area-m2",
            "class": {"column": "kind", "values": ["a"]},
        },
    ]
    result = output(run(tmp_path, inputs, spec_for(measures, crs)), "many-indicators")
    assert result["indicators"]["a-ha"] == pytest.approx(1.5, abs=0.0001)
    assert result["indicators"]["a-m2"] == pytest.approx(15_000, abs=0.5)
    metadata = result["metadata"]["vectorAggregate"]
    assert metadata["areaCrs"] == crs
    assert metadata["boundaryAreaM2"] == pytest.approx(20_000, abs=0.01)


def test_relations_count_different_features(tmp_path: Path) -> None:
    inputs = build(tmp_path)
    measures = [
        {
            "indicatorId": f"count-{relation}",
            "measure": "count",
            "relation": relation,
            "class": {"column": "kind", "values": ["r"]},
        }
        for relation in RELATION_COUNTS
    ]
    result = output(run(tmp_path, inputs, spec_for(measures)), "many-indicators")
    assert result["indicators"] == {
        f"count-{relation}": count for relation, count in RELATION_COUNTS.items()
    }
    details = result["metadata"]["vectorAggregate"]["measures"]["count-contains"]
    assert details["relation"] == "within"
    assert details["declaredRelation"] == "contains"


def test_attribute_sums_are_whole_feature_and_distinct_values_ignore_nulls(
    tmp_path: Path,
) -> None:
    inputs = build(tmp_path)
    class_r = {"column": "kind", "values": ["r"]}
    measures = [
        # Whole-feature values of every intersecting feature, never apportioned:
        # inside 1+2, touching 4, mostly-out 8+16, mostly-in 32, covering 64, ell 128.
        {
            "indicatorId": "credits-r",
            "measure": "sum-attribute",
            "attribute": "credits",
            "class": class_r,
        },
        {
            "indicatorId": "credits-within",
            "measure": "sum-attribute",
            "attribute": "credits",
            "relation": "within",
            "class": class_r,
        },
        # a2 has no value: NaN counts as 0 and is recorded.
        {
            "indicatorId": "credits-a",
            "measure": "sum-attribute",
            "attribute": "credits",
            "class": {"column": "kind", "values": ["a"]},
        },
        {
            "indicatorId": "units-r",
            "measure": "sum-attribute",
            "attribute": "units",
            "class": class_r,
        },
        # x, y, z (touching has no species).
        {
            "indicatorId": "species-r",
            "measure": "distinct-attribute",
            "attribute": "species",
            "class": class_r,
        },
    ]
    result = output(run(tmp_path, inputs, spec_for(measures)), "many-indicators")
    values = result["indicators"]
    assert values["credits-r"] == 255.0
    assert values["credits-within"] == 3.0
    assert values["credits-a"] == 10.0
    assert values["units-r"] == sum(range(4, 12)) and isinstance(values["units-r"], int)
    assert values["species-r"] == 3
    measures_meta = result["metadata"]["vectorAggregate"]["measures"]
    assert measures_meta["credits-a"]["nullAttributeCount"] == 1
    assert measures_meta["species-r"]["nullAttributeCount"] == 1
    assert measures_meta["credits-r"]["featureCount"] == 8


def test_known_source_without_features_gives_zero_and_records_counts(
    tmp_path: Path,
) -> None:
    inputs = build(tmp_path)
    measures = [
        {
            "indicatorId": "sum-r-area",
            "measure": "area-m2",
            "class": {"column": "kind", "values": ["r"]},
        },
        {
            "indicatorId": "count-r",
            "measure": "count",
            "class": {"column": "kind", "values": ["r"]},
        },
    ]
    target = run(tmp_path, inputs, spec_for(measures), "--geometry-id", "empty")
    result = output(target, "many-indicators", "empty")
    assert result["indicators"] == {"sum-r-area": 0.0, "count-r": 0}
    metadata = result["metadata"]["vectorAggregate"]
    assert metadata["intersectingFeatureCount"] == 0
    assert metadata["outsideClassificationFeatureCount"] == 0


def test_features_outside_the_declared_classification_are_recorded(
    tmp_path: Path,
) -> None:
    inputs = build(tmp_path)
    spec = spec_for(
        [
            {
                "indicatorId": "count-a",
                "measure": "count",
                "class": {"column": "kind", "values": ["a"]},
            }
        ],
        classification={"column": "kind", "values": ["a", "b"]},
    )
    result = output(run(tmp_path, inputs, spec), "count-a")
    metadata = result["metadata"]["vectorAggregate"]
    assert metadata["outsideClassificationFeatureCount"] == 8  # the class-r features
    assert metadata["intersectingFeatureCount"] == 11


def test_single_measure_output_matches_process_geometry_format(tmp_path: Path) -> None:
    inputs = build(tmp_path)
    spec = spec_for(
        [
            {
                "indicatorId": "count-a",
                "measure": "count",
                "class": {"column": "kind", "values": ["a"]},
            }
        ]
    )
    target = run(tmp_path, inputs, spec)
    result = output(target, "count-a")
    assert set(result) == {
        "id",
        "productId",
        "productRunId",
        "geometryOutputId",
        "timePoint",
        "indicators",
        "metadata",
    }
    assert result["timePoint"] == "2024-06-30T00:00:00Z"
    assert result["geometryOutputId"] == "main"
    assert result["metadata"]["geometryProvenanceUrl"] == inputs["geometry"]
    assert result["metadata"]["datasetProvenanceUrl"] == inputs["dataset"]
    method = json.loads((target / "count-a" / "p.method.json").read_text())
    assert method["specification"] == spec
    assert method["partitionSource"].endswith("/partition/")
    assert method["catalogueVerification"].startswith("offline")
    assert set(method["relationDefinitions"]) == {
        "intersects",
        "within",
        "centroid-within",
        "representative-point-within",
        "covers-boundary",
    }


def test_geometry_ids_all_geometries_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = build(tmp_path)
    spec = spec_for(
        [
            {
                "indicatorId": "sum-a-area",
                "measure": "area-m2",
                "class": {"column": "kind", "values": ["a"]},
            }
        ]
    )
    batch = run(
        tmp_path, inputs, spec, "--geometry-ids", "main,half", target=tmp_path / "batch"
    )
    everything = run(
        tmp_path, inputs, spec, "--all-geometries", target=tmp_path / "all"
    )
    for target in (batch, everything):
        assert output(target, "sum-a-area")["indicators"][
            "sum-a-area"
        ] == pytest.approx(15_000, abs=0.5)
        assert output(target, "sum-a-area", "half")["indicators"][
            "sum-a-area"
        ] == pytest.approx(10_000, abs=0.5)
    assert sorted(
        p.name for p in (everything / "sum-a-area" / "2024-06-30").iterdir()
    ) == ["p-empty.json", "p-half.json", "p-main.json"]

    finished = batch / "sum-a-area" / "2024-06-30" / "p-main.json"
    finished.write_text(json.dumps({"sentinel": True}))
    (batch / "sum-a-area" / "2024-06-30" / "p-half.json").unlink()
    measured = []
    import csdr.vector_aggregate as engine

    real = engine.aggregate_boundary

    def recording(*args: object, **kwargs: object) -> tuple:
        measured.append(args[2].bounds)
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "aggregate_boundary", recording)
    run(tmp_path, inputs, spec, "--geometry-ids", "main,half,empty", target=batch)
    assert json.loads(finished.read_text()) == {"sentinel": True}
    assert len(measured) == 2  # half and empty; main is finished
    # A fully finished batch reads no inputs.
    monkeypatch.setattr(
        engine, "read_provenance", lambda url: pytest.fail("finished batch read inputs")
    )
    run(tmp_path, inputs, spec, "--geometry-ids", "main,half,empty", target=batch)


def test_a_different_specification_cannot_mix_into_a_target(tmp_path: Path) -> None:
    inputs = build(tmp_path)
    spec = spec_for(
        [
            {
                "indicatorId": "count-a",
                "measure": "count",
                "class": {"column": "kind", "values": ["a"]},
            }
        ]
    )
    target = run(tmp_path, inputs, spec)
    changed = copy.deepcopy(spec)
    changed["countingRule"] = "A different rule."
    with pytest.raises(CSDRException, match="different specification"):
        run(tmp_path, inputs, changed, "--geometry-id", "half", target=target)
    run(
        tmp_path,
        inputs,
        changed,
        "--geometry-id",
        "half",
        target=target,
        extra=("--overwrite",),
    )


@pytest.mark.parametrize(
    "ids,message",
    [
        (("--geometry-id", "main", "--all-geometries"), "exactly one"),
        (("--geometry-ids", "main,main"), "repeats ids"),
        (("--geometry-id", "unknown"), "not found"),
    ],
)
def test_geometry_selection_is_validated(
    tmp_path: Path, ids: tuple[str, ...], message: str
) -> None:
    inputs = build(tmp_path)
    spec = spec_for([{"indicatorId": "count-a", "measure": "count"}])
    with pytest.raises(CSDRException, match=message):
        run(tmp_path, inputs, spec, *ids)


def test_run_id_must_match_and_partitions_are_required(tmp_path: Path) -> None:
    inputs = build(tmp_path)
    spec = spec_for([{"indicatorId": "count-a", "measure": "count"}])
    with pytest.raises(CSDRException, match="productRunId"):
        run(tmp_path, inputs, spec, extra=("--run-id", "other"))
    for file in (tmp_path / "dataset" / "partition").iterdir():
        file.unlink()
    (tmp_path / "dataset" / "partition").rmdir()
    with pytest.raises(CSDRException, match="partition"):
        run(tmp_path, inputs, spec)


def valid_measures() -> list[dict]:
    return [
        {
            "indicatorId": "count-a",
            "measure": "count",
            "class": {"column": "kind", "values": ["a"]},
        }
    ]


@pytest.mark.parametrize(
    "change,message",
    [
        (lambda s: s.update(extra=1), "without unknown fields"),
        (lambda s: s.pop("countingRule"), "without unknown fields"),
        (lambda s: s.update(version=True), "without unknown fields"),
        (lambda s: s.update(areaCrs="EPSG:4326"), "EPSG:3577 or EPSG:6933"),
        (lambda s: s.update(timePoint="2024"), "ISO date"),
        (lambda s: s.update(evidenceClass="guess"), "evidenceClass"),
        (lambda s: s.update(overlapPolicy="max"), "overlapPolicy"),
        (lambda s: s.update(countingRule=" "), "counting rule"),
        (lambda s: s["measures"][0].update(relation="near"), "relation must be one of"),
        (lambda s: s["measures"][0].update(measure="mean"), "measure must be one of"),
        (lambda s: s["measures"][0].update(unit="ha"), "no other fields"),
        (lambda s: s["measures"][0].update(indicatorId="a/b"), "indicatorId"),
        (
            lambda s: s["measures"].append(dict(s["measures"][0])),
            "more than one measure",
        ),
        (
            lambda s: s["measures"][0].update(
                {"class": {"column": "kind", "values": ["q"]}}
            ),
            "declared classification",
        ),
        (
            lambda s: s["measures"][0].update(
                {"class": {"column": "other", "values": ["a"]}}
            ),
            "declared classification",
        ),
        (
            lambda s: s["measures"][0].update(
                {"class": {"column": "kind", "values": ["a", 1]}}
            ),
            "all strings or all finite numbers",
        ),
        (
            lambda s: s["measures"][0].update(
                {"class": {"column": "kind", "values": []}}
            ),
            "non-empty",
        ),
        (lambda s: s["measures"][0].update(measure="sum-attribute"), "attribute"),
        (lambda s: s["measures"][0].update(attribute="credits"), "only used by"),
        (
            lambda s: s["measures"][0].update(measure="area-m2", relation="within"),
            "relation is intersects",
        ),
        (
            lambda s: s.update(classification={"column": "kind"}),
            "exactly 'column' and 'values'",
        ),
        (lambda s: s.update(measures=[]), "between 1 and"),
    ],
)
def test_specification_refusals(change: object, message: str) -> None:
    spec = spec_for(valid_measures())
    change(spec)
    with pytest.raises(CSDRException, match=message):
        validate_vector_aggregate_spec(spec)


def test_specification_defaults_and_null_classification() -> None:
    spec = spec_for(
        [
            {
                "indicatorId": "count-x",
                "measure": "count",
                "class": {"column": "status", "values": ["Active"]},
            }
        ],
        classification=None,
    )
    plan = validate_vector_aggregate_spec(spec)
    assert plan["overlapPolicy"] == "union"
    assert plan["measures"][0]["relation"] == "intersects"


@pytest.mark.parametrize(
    "measure,message",
    [
        (
            {
                "indicatorId": "x",
                "measure": "count",
                "class": {"column": "missing", "values": ["a"]},
            },
            "not in the dataset",
        ),
        (
            {"indicatorId": "x", "measure": "sum-attribute", "attribute": "species"},
            "numeric column",
        ),
        (
            {
                "indicatorId": "x",
                "measure": "count",
                "class": {"column": "units", "values": ["1"]},
            },
            "column type",
        ),
    ],
)
def test_columns_are_checked_against_the_dataset(
    tmp_path: Path, measure: dict, message: str
) -> None:
    inputs = build(tmp_path)
    with pytest.raises(CSDRException, match=message):
        run(tmp_path, inputs, spec_for([measure], classification=None))


def consolidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, pd.DataFrame]:
    """fixture -> vector-aggregate (batch) -> consolidate; the consolidated frame."""
    inputs = build(tmp_path)
    spec = spec_for(
        [
            {
                "indicatorId": "sum-a-area",
                "measure": "area-m2",
                "class": {"column": "kind", "values": ["a"]},
            },
            {
                "indicatorId": "count-r",
                "measure": "count",
                "class": {"column": "kind", "values": ["r"]},
                "relation": "within",
            },
            {
                "indicatorId": "species-r",
                "measure": "distinct-attribute",
                "attribute": "species",
                "class": {"column": "kind", "values": ["r"]},
            },
        ]
    )
    monkeypatch.setattr(
        "sys.argv",
        ["csdr", "products", "vector-aggregate", "--geometry-ids", "main,half,empty"],
    )
    target = run(tmp_path, inputs, spec, "--geometry-ids", "main,half,empty")
    result = runner.invoke(
        products_app,
        [
            "consolidate",
            "--product-id",
            "p",
            "--location",
            str(target),
            "--geometry-provenance-url",
            inputs["geometry"],
            "--dataset-provenance-url",
            inputs["dataset"],
            "--indicator-name",
            "many-indicators",
        ],
    )
    assert result.exit_code == 0, result.output
    return target, pd.read_parquet(target / "many-indicators" / "p.parquet")


def test_consolidate_and_parse_outputs_end_to_end(
    tmp_path: Path, steps_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, frame = consolidated(tmp_path, monkeypatch)
    assert sorted(frame["geometryOutputId"]) == ["empty", "half", "main"]
    outputs = parse_outputs(frame)
    by_geometry = {
        row["geometryOutputId"]: row
        for row in outputs["sum-a-area"]["2024-06-30T00:00:00Z"]
    }
    assert by_geometry["main"]["value"] == pytest.approx(15_000, abs=0.5)
    assert by_geometry["empty"]["value"] == 0.0
    main_count = [
        r
        for r in outputs["count-r"]["2024-06-30T00:00:00Z"]
        if r["geometryOutputId"] == "main"
    ]
    assert main_count[0]["value"] == 2

    summary = json.loads((steps_dir / "step-0000.json").read_text())
    assert summary["label"].startswith("Aggregate vector features for 3 geometries")
    assert summary["source"]["function"] == "vector_aggregate_command"
    assert "--geometry-ids {geometry_ids}" in summary["command"]
    assert summary["inputs"]["specification_sha256"]
    assert json.loads((steps_dir / "step-0001.json").read_text())["label"].startswith(
        "Consolidate"
    )


def test_parse_outputs_carries_the_counting_rule_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, frame = consolidated(tmp_path, monkeypatch)
    outputs = parse_outputs(frame)
    by_geometry = {
        row["geometryOutputId"]: row
        for row in outputs["sum-a-area"]["2024-06-30T00:00:00Z"]
    }
    meta = by_geometry["main"]["metadata"]["vectorAggregate"]
    assert meta["measures"]["count-r"]["relation"] == "within"
    assert meta["measures"]["sum-a-area"]["class"] == {
        "column": "kind",
        "values": ["a"],
    }
    json.dumps(by_geometry["main"]["metadata"], allow_nan=False)


# --- Catalogue verification (mocked) ----------------------------------------------


class Reader:
    hostname = "https://catalogue.example.test"
    organization_id = None

    def __init__(self, inputs: dict[str, str], units: dict[str, str]) -> None:
        data_url = json.loads(Path(inputs["dataset"]).read_text())["dataUrl"]
        self.records = {
            ("product", "p"): {
                "id": "p",
                "dataset": {"id": "d"},
                "geometries": {"id": "g"},
            },
            ("dataset-run", "dr"): {
                "id": "dr",
                "dataset": {"id": "d"},
                "dataType": "geoparquet",
                "dataUrl": data_url,
            },
            ("geometries-run", "gr"): {"id": "gr", "geometries": {"id": "g"}},
            **{
                ("indicator/measured", key): {"id": key, "unit": unit}
                for key, unit in units.items()
            },
        }
        self.calls = []

    def read(
        self, resource: str, identifier: str, *, missing_ok: bool = False
    ) -> dict | None:
        self.calls.append((resource, identifier))
        if missing_ok:
            return copy.deepcopy(self.records.get((resource, identifier)))
        return copy.deepcopy(self.records[(resource, identifier)])


AREA_MEASURES = [
    {
        "indicatorId": "a-ha",
        "measure": "area-ha",
        "class": {"column": "kind", "values": ["a"]},
    },
    {
        "indicatorId": "a-m2",
        "measure": "area-m2",
        "class": {"column": "kind", "values": ["a"]},
    },
    {"indicatorId": "credits", "measure": "sum-attribute", "attribute": "credits"},
]


def verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, units: dict
) -> tuple[Reader, dict]:
    inputs = build(tmp_path)
    reader = Reader(inputs, units)
    monkeypatch.setattr(CatalogueReader, "from_environment", lambda: reader)
    return reader, inputs


def test_verified_run_checks_chain_and_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader, inputs = verified(
        tmp_path, monkeypatch, {"a-ha": "ha", "a-m2": "m2", "credits": "credits"}
    )
    target = run(tmp_path, inputs, spec_for(AREA_MEASURES), offline=False)
    result = output(target, "many-indicators")
    check = result["metadata"]["vectorAggregate"]["catalogueVerification"]
    assert check["status"] == "catalogue-inputs-matched"
    assert check["indicatorUnits"] == {"a-ha": "ha", "a-m2": "m2", "credits": "credits"}
    assert len(check["snapshotSha256"]) == 64
    assert ("product-run", "r") in reader.calls
    method = json.loads((target / "many-indicators" / "p.method.json").read_text())
    assert method["areaHaUnitCheck"] == "catalogue unit ha"


@pytest.mark.parametrize(
    "change,message",
    [
        ("area-ha-unit", "area-ha needs a measured indicator with unit ha"),
        ("area-m2-unit", "declare area-ha, not area-m2"),
        ("chain", "same catalogue chain"),
        ("existing-run", "fresh product run"),
        ("data-url", "dataset provenance file"),
        ("missing-indicator", "indicator/measured"),
    ],
)
def test_catalogue_refusals_write_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str, message: str
) -> None:
    reader, inputs = verified(
        tmp_path, monkeypatch, {"a-ha": "ha", "a-m2": "m2", "credits": "credits"}
    )
    if change == "area-ha-unit":
        reader.records[("indicator/measured", "a-ha")]["unit"] = "m2"
    elif change == "area-m2-unit":
        reader.records[("indicator/measured", "a-m2")]["unit"] = "ha"
    elif change == "chain":
        reader.records[("dataset-run", "dr")]["dataset"]["id"] = "other"
    elif change == "existing-run":
        reader.records[("product-run", "r")] = {"id": "r"}
    elif change == "data-url":
        reader.records[("dataset-run", "dr")]["dataUrl"] = "/elsewhere.parquet"
    elif change == "missing-indicator":
        del reader.records[("indicator/measured", "credits")]
    with pytest.raises((CSDRException, KeyError), match=message):
        run(tmp_path, inputs, spec_for(AREA_MEASURES), offline=False)
    assert not list((tmp_path / "product").rglob("*.json"))


def test_catalogue_change_during_calculation_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader, inputs = verified(
        tmp_path, monkeypatch, {"a-ha": "ha", "a-m2": "m2", "credits": "credits"}
    )
    real = reader.read

    def read(
        resource: str, identifier: str, *, missing_ok: bool = False
    ) -> dict | None:
        if len(reader.calls) >= 7:  # the second (after-calculation) snapshot
            reader.records[("indicator/measured", "credits")]["updatedAt"] = "later"
        return real(resource, identifier, missing_ok=missing_ok)

    monkeypatch.setattr(reader, "read", read)
    with pytest.raises(CSDRException, match="changed during calculation"):
        run(tmp_path, inputs, spec_for(AREA_MEASURES), offline=False)
    assert not list((tmp_path / "product").rglob("*.json"))


def test_offline_allows_area_ha_unchecked_and_never_calls_catalogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader, inputs = verified(tmp_path, monkeypatch, {})
    target = run(tmp_path, inputs, spec_for(AREA_MEASURES))
    assert not reader.calls
    method = json.loads((target / "many-indicators" / "p.method.json").read_text())
    assert method["areaHaUnitCheck"] == "unchecked (offline)"
    result = output(target, "many-indicators")
    assert result["metadata"]["vectorAggregate"]["catalogueVerification"] == {
        "status": "not-verified"
    }


# --- Clips that are not single polygons, and reserved aliases ---------------------


def build_layer(tmp_path: Path, geometries: list, **columns: list) -> dict[str, str]:
    """A one-class layer and one boundary, both in EPSG:4326.

    Boundary and features pass through the same EPSG:4326 -> areaCrs projection,
    so a feature sharing an edge or corner with the boundary touches it exactly
    and its clip is a line or point.
    """
    features = gpd.GeoDataFrame(
        {"kind": ["a"] * len(geometries), **columns}, geometry=geometries, crs=4326
    )
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    partition_parquet(
        get_store_with_prefix_from_url(str(dataset)), features, file_prefix="features"
    )
    features.to_parquet(dataset / "features.parquet")
    (dataset / "features.parquet.provenance.json").write_text(
        json.dumps(
            {"dataUrl": str(dataset / "features.parquet"), "dataType": "geoparquet"}
        )
    )
    gpd.GeoDataFrame(
        {"csdr-id": ["main"], "csdr-name": ["Main"]},
        geometry=[box(148.0, -34.0, 149.0, -33.0)],
        crs=4326,
    ).to_parquet(tmp_path / "boundaries.parquet")
    (tmp_path / "boundaries.parquet.provenance.json").write_text(
        json.dumps({"dataUrl": str(tmp_path / "boundaries.parquet")})
    )
    return {
        "dataset": str(dataset / "features.parquet.provenance.json"),
        "geometry": str(tmp_path / "boundaries.parquet.provenance.json"),
    }


INSIDE = box(148.1, -33.9, 148.2, -33.8)
SECOND_INSIDE = box(148.3, -33.9, 148.4, -33.8)
EDGE_TOUCHING = box(149.0, -33.5, 149.5, -33.4)  # clip is a line on 149 E
CORNER_TOUCHING = box(149.0, -33.0, 149.5, -32.5)  # clip is the point (149, -33)
INSIDE_AND_TOUCHING = INSIDE.union(EDGE_TOUCHING)  # MultiPolygon; clip is a collection
INSIDE_M2 = float(gpd.GeoSeries([INSIDE], crs=4326).to_crs(3577).area.iloc[0])
SECOND_M2 = float(gpd.GeoSeries([SECOND_INSIDE], crs=4326).to_crs(3577).area.iloc[0])


@pytest.mark.parametrize("policy", ["union", "sum-features"])
@pytest.mark.parametrize(
    "geometries,area,features",
    [
        ([INSIDE, EDGE_TOUCHING], INSIDE_M2, 2),
        ([INSIDE, CORNER_TOUCHING], INSIDE_M2, 2),
        ([EDGE_TOUCHING], 0.0, 1),
        ([INSIDE_AND_TOUCHING], INSIDE_M2, 1),
        ([INSIDE_AND_TOUCHING, SECOND_INSIDE], INSIDE_M2 + SECOND_M2, 2),
    ],
    ids=[
        "inside+edge",
        "inside+corner",
        "edge-only",
        "multipolygon",
        "multipolygon+inside",
    ],
)
def test_touching_clips_keep_only_polygon_area(
    tmp_path: Path, policy: str, geometries: list, area: float, features: int
) -> None:
    assert INSIDE_AND_TOUCHING.geom_type == "MultiPolygon"
    inputs = build_layer(tmp_path, geometries)
    spec = spec_for(
        [{"indicatorId": "sum-a-area", "measure": "area-m2"}],
        classification=None,
        overlapPolicy=policy,
    )
    result = output(run(tmp_path, inputs, spec), "sum-a-area")
    assert result["indicators"]["sum-a-area"] == pytest.approx(area, rel=1e-6, abs=1e-6)
    details = result["metadata"]["vectorAggregate"]["measures"]["sum-a-area"]
    assert details["featureCount"] == features


def test_dataset_columns_named_like_engine_aliases_are_harmless(
    tmp_path: Path,
) -> None:
    names = ["p", "g", "valid", "wkb", "c0", "r_intersects"]
    inputs = build_layer(
        tmp_path,
        [INSIDE, SECOND_INSIDE],
        **{name: ["x", "y"] for name in names},
    )
    spec = spec_for(
        [
            {
                "indicatorId": "area-p-x",
                "measure": "area-m2",
                "class": {"column": "p", "values": ["x"]},
            },
            {
                "indicatorId": "count-g-y",
                "measure": "count",
                "class": {"column": "g", "values": ["y"]},
            },
            {
                "indicatorId": "distinct-c0",
                "measure": "distinct-attribute",
                "attribute": "c0",
                "relation": "representative-point-within",
            },
            {
                "indicatorId": "count-within",
                "measure": "count",
                "relation": "within",
                "class": {"column": "r_intersects", "values": ["x", "y"]},
            },
        ],
        classification=None,
    )
    values = output(run(tmp_path, inputs, spec), "many-indicators")["indicators"]
    assert values["area-p-x"] == pytest.approx(INSIDE_M2, rel=1e-6)
    assert values == {
        "area-p-x": values["area-p-x"],
        "count-g-y": 1,
        "distinct-c0": 2,
        "count-within": 2,
    }
