"""Multipart mapped-area sums add the valid cells of each part.

A part with no valid cell contributes 0.0 (with a warning), as sum-<x>-area
always has; the zonal statistics families refuse such a boundary instead
(tests/test_zonal_statistics.py).
"""

import logging
from unittest.mock import patch

import numpy as np
import pytest
from odc.geo.geobox import GeoBox
from odc.geo.geom import Geometry
from odc.geo.xr import wrap_xr
from shapely.geometry import MultiPolygon, Polygon, box

from csdr.products import process_indicators_for_geometry
from csdr.utils import xarray_calculate_area_m2


def calculate(
    values: list[list[int]],
    *,
    reverse: bool = False,
    indicators: dict | None = None,
    all_touched: bool = False,
    parts: list[Polygon] | None = None,
) -> dict:
    grid = GeoBox.from_bbox((0, 0, 60, 20), crs="EPSG:6933", resolution=10)
    data = wrap_xr(np.array(values, dtype="uint8"), grid, nodata=255)
    parts = parts or [box(0, 0, 20, 20), box(40, 0, 60, 20)]
    geometry = Geometry(
        MultiPolygon(list(reversed(parts)) if reverse else parts), crs=6933
    ).to_crs(4326)

    def measure(
        sd: object, url: str, kind: str, part: Geometry, **kwargs: object
    ) -> float:
        return xarray_calculate_area_m2(
            data,
            part,
            value_list=kwargs["value_list"],
            all_touched=kwargs["raster_all_touched"],
        )

    with (
        patch("csdr.products.sedona.db.connect"),
        patch(
            "csdr.products.read_provenance",
            return_value={"dataType": "stac-geoparquet", "dataUrl": "fixture.parquet"},
        ),
        patch("csdr.products._get_area_m2_from_dataset_geometry", side_effect=measure),
    ):
        return process_indicators_for_geometry(
            geometry,
            indicators
            or {
                "sum-mangrove-area": {
                    "indicator-name": "classification",
                    "indicator-value": 3,
                },
                "sum-saltmarsh-area": {
                    "indicator-name": "classification",
                    "indicator-value": 4,
                },
            },
            "fixture-provenance.json",
            raster_all_touched=all_touched,
        )


MIXED = [[3, 4, 255, 255, 255, 255], [3, 4, 255, 255, 255, 255]]


@pytest.mark.parametrize("reverse", [False, True])
def test_a_part_without_valid_cells_adds_zero_with_a_warning(
    reverse: bool, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="csdr.utils"):
        result = calculate(MIXED, reverse=reverse)
    assert result == {"sum-mangrove-area": 200.0, "sum-saltmarsh-area": 200.0}
    assert "No valid raster cells in this part of the geometry" in caplog.text


def test_wholly_unsupported_geometry_is_reported_as_zero_area() -> None:
    result = calculate([[255] * 6] * 2)
    assert result == {"sum-mangrove-area": 0.0, "sum-saltmarsh-area": 0.0}


def test_valid_class_absence_is_zero() -> None:
    result = calculate([[2, 2, 255, 255, 255, 255]] * 2)
    assert result == {"sum-mangrove-area": 0.0, "sum-saltmarsh-area": 0.0}


def test_fully_supported_multipart_results_sum_over_parts() -> None:
    values = [[3, 4, 255, 255, 3, 4]] * 2
    assert calculate(values) == {
        "sum-mangrove-area": 400.0,
        "sum-saltmarsh-area": 400.0,
    }


def test_cell_rule_reaches_every_part() -> None:
    # Each part touches one and a half columns: all-touched burns both columns
    # of each part, the cell-centre rule only the column whose centre is inside.
    parts = [box(0, 0, 14, 20), box(40, 0, 54, 20)]
    values = [[3, 3, 255, 255, 3, 3]] * 2
    indicators = {"sum-mapped-area": {"indicator-name": "classification"}}
    touched = calculate(values, indicators=indicators, all_touched=True, parts=parts)
    centre = calculate(values, indicators=indicators, all_touched=False, parts=parts)
    assert touched == {"sum-mapped-area": 800.0}
    assert centre == {"sum-mapped-area": 400.0}
