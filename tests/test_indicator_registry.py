"""Indicator registry: families, exact-id allow-list, ordering and declared families."""

import json

import pytest
from odc.geo.geom import Geometry
from shapely.geometry import box
from typer.testing import CliRunner

from csdr.cli_helpers import helpers_app
from csdr.cli_products import _validate_parameters
from csdr.indicators import (
    INDICATOR_FAMILIES,
    KNOWN_INDICATORS,
    _family,
    family_for,
    order_indicators,
)
from csdr.products import process_indicators_for_geometry
from csdr.utils import CSDRException

SAMPLE_IDS = {
    "sum-area": "sum-wetland-area",
    "percent-area": "percent-wetland-area",
    "count": "count-sites",
    "mean": "mean-condition",
    "p10": "p10-condition",
    "p25": "p25-condition",
    "p50": "p50-condition",
    "p75": "p75-condition",
    "p90": "p90-condition",
    "valid-fraction": "valid-fraction-condition",
    "distinct-count": "distinct-class-count",
}


def test_every_exact_id_also_matches_an_implemented_family() -> None:
    for indicator in KNOWN_INDICATORS:
        family = family_for(indicator)
        assert family is not None and family.implemented, indicator


def test_sample_ids_match_exactly_one_family() -> None:
    assert {family.name for family in INDICATOR_FAMILIES} == set(SAMPLE_IDS)
    for name, indicator in SAMPLE_IDS.items():
        matches = [f.name for f in INDICATOR_FAMILIES if f.pattern.fullmatch(indicator)]
        assert matches == [name]


def test_every_family_is_implemented() -> None:
    status = {family.name: family.status for family in INDICATOR_FAMILIES}
    assert status == dict.fromkeys(SAMPLE_IDS, "implemented")
    handlers = {family.name: family.handler for family in INDICATOR_FAMILIES}
    for name in (
        "mean",
        "p10",
        "p25",
        "p50",
        "p75",
        "p90",
        "valid-fraction",
        "distinct-count",
    ):
        assert handlers[name] == "zonal-statistics"


def test_validation_accepts_any_family_id_including_declared_ones() -> None:
    assert _validate_parameters(list(SAMPLE_IDS.values()), "2024", None) == "2024"
    assert _validate_parameters(KNOWN_INDICATORS, None, "2024") == "2024"


def test_unknown_indicator_error_lists_families_and_exact_ids() -> None:
    with pytest.raises(CSDRException) as error:
        _validate_parameters(["sum-wetland-area", "max-height"], "2024", None)
    message = str(error.value)
    assert "['max-height']" in message
    assert "sum-<x>-area (implemented)" in message
    assert "distinct-<x>-count (implemented)" in message
    assert "sum-mangrove-area" in message
    assert "csdr helpers indicator-families" in message


def test_dependent_families_run_after_the_family_they_need() -> None:
    ordered = order_indicators(
        {"percent-a-area": {}, "sum-z-area": {}, "count-b": {}, "mean-c": {}}
    )
    assert list(ordered) == ["count-b", "mean-c", "sum-z-area", "percent-a-area"]


def test_declared_family_is_refused_before_any_source_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every shipped family has a handler; a family a future build declares first
    # must still be refused before any source is read.
    declared = _family("max", "max-<x>", r"^max-.+$", None, "Maximum (declared).")
    monkeypatch.setattr(
        "csdr.indicators.INDICATOR_FAMILIES", (*INDICATOR_FAMILIES, declared)
    )
    monkeypatch.setattr(
        "csdr.products.read_provenance",
        lambda url: pytest.fail("No source may be read for an unimplemented family"),
    )
    assert _validate_parameters(["max-height"], "2024", None) == "2024"
    with pytest.raises(
        CSDRException,
        match=r"^indicator family max-<x> is registered but has no handler in this build$",
    ):
        process_indicators_for_geometry(
            Geometry(box(0, 0, 1, 1), crs="EPSG:4326"),
            {"sum-wetland-area": {}, "max-height": {"indicator-name": "x"}},
            "unused.provenance.json",
        )


def test_percent_uses_the_sum_from_the_same_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "csdr.products.read_provenance",
        lambda url: {"dataUrl": "unused.parquet", "dataType": "geoparquet"},
    )
    seen = []

    def area(*args: object, **kwargs: object) -> float:
        seen.append(kwargs["indicator"])
        return 1_000.0

    monkeypatch.setattr("csdr.products._get_area_m2_from_dataset_geometry", area)
    geometry = Geometry(box(0, 0, 100, 100), crs="EPSG:6933").to_crs("EPSG:4326")
    # Percent is listed first; the registry still computes the sum first.
    results = process_indicators_for_geometry(
        geometry,
        {
            "percent-wetland-area": {"indicator-name": "wetland"},
            "sum-wetland-area": {"indicator-name": "wetland"},
        },
        "unused.provenance.json",
    )
    assert seen == ["wetland"]
    assert results["sum-wetland-area"] == 1_000.0
    assert results["percent-wetland-area"] == pytest.approx(10.0, rel=1e-6)


def test_helpers_prints_the_registry_as_a_table_and_json() -> None:
    runner = CliRunner()
    table = runner.invoke(helpers_app, ["indicator-families"])
    assert table.exit_code == 0, table.output
    assert "valid-fraction-<x>  implemented" in table.output
    assert "percent-<x>-area    implemented  sum-<x>-area" in table.output
    assert "sum-mangrove-area" in table.output

    as_json = runner.invoke(helpers_app, ["indicator-families", "--json"])
    assert as_json.exit_code == 0, as_json.output
    document = json.loads(as_json.output)
    assert document["exactIds"] == KNOWN_INDICATORS
    assert [row["idPattern"] for row in document["families"]] == [
        family.template for family in INDICATOR_FAMILIES
    ]
    assert document["families"][3] == {
        "family": "mean",
        "idPattern": "mean-<x>",
        "regex": "^mean-.+$",
        "status": "implemented",
        "handler": "zonal-statistics",
        "dependsOn": None,
        "description": "Mean of valid raster cells within the geometry (float64 "
        "sum / valid cells).",
    }
