"""Small independent grids: valid cells, nodata and the boundary cell rule.

A cell is valid when it is finite and not the declared nodata value; value
selection applies to valid cells only. The area sum keeps reporting a boundary
without valid cells as 0.0 (with a warning).
"""

import logging

import numpy as np
import pytest
from odc.geo.geobox import GeoBox
from odc.geo.geom import Geometry
from odc.geo.xr import wrap_xr
from shapely.geometry import box

from csdr.utils import xarray_calculate_area_m2

NO_COVERAGE_WARNING = "No valid raster cells in this part of the geometry"


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize(
    ("values", "selected_class", "expected"),
    [
        ([[255, 255], [255, 255]], [3], None),
        ([[np.nan, np.inf], [-np.inf, np.nan]], [3], None),
        ([[255, 255], [255, 255]], None, None),
        ([[2, 2], [2, 2]], [3], 0),
        ([[3, 2], [255, 2]], [3], 100),
        ([[3, 2], [255, 2]], None, 300),
        ([[0, 2], [255, 2]], [0], 100),
    ],
)
def test_raster_source_support_and_class_area(
    values: list[list[float]],
    selected_class: list[float] | None,
    expected: float | None,
    chunked: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Four 10 m by 10 m cells in an equal-area CRS. Expected areas are independent
    # counts of known cells, not a second call to the processing implementation.
    geobox = GeoBox.from_bbox((0, 0, 20, 20), crs="EPSG:6933", resolution=10)
    data = wrap_xr(np.array(values), geobox, nodata=255)
    if chunked:
        data = data.chunk({"x": 1, "y": 1})
    geometry = geobox.extent.to_crs(4326)

    with caplog.at_level(logging.WARNING, logger="csdr.utils"):
        area = xarray_calculate_area_m2(data, geometry, value_list=selected_class)
    if expected is None:
        # No valid cell: the area path reports 0.0 and warns.
        assert area == 0.0
        assert NO_COVERAGE_WARNING in caplog.text
    else:
        assert area == expected
        assert NO_COVERAGE_WARNING not in caplog.text


def test_nodata_inside_the_boundary_is_never_counted_without_a_selection() -> None:
    # Before the validity rule, a non-NaN nodata value counted as area when no
    # indicator-value was given.
    geobox = GeoBox.from_bbox((0, 0, 20, 20), crs="EPSG:6933", resolution=10)
    data = wrap_xr(np.array([[1, 255], [255, 255]], dtype="uint8"), geobox, nodata=255)
    assert xarray_calculate_area_m2(data, geobox.extent.to_crs(4326)) == 100


def test_valid_cells_outside_the_boundary_do_not_establish_coverage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    geobox = GeoBox.from_bbox((0, 0, 20, 20), crs="EPSG:6933", resolution=10)
    data = wrap_xr(np.array([[3, 255], [3, 255]], dtype="uint8"), geobox, nodata=255)
    right_column = Geometry(box(10, 0, 20, 20), crs="EPSG:6933").to_crs(4326)

    with caplog.at_level(logging.WARNING, logger="csdr.utils"):
        assert xarray_calculate_area_m2(data, right_column, value_list=[3]) == 0.0
    assert NO_COVERAGE_WARNING in caplog.text


@pytest.mark.parametrize("chunked", [False, True])
def test_explicit_cell_centre_rule_preserves_the_existing_touched_cell_default(
    chunked: bool,
) -> None:
    geobox = GeoBox.from_bbox((0, 0, 20, 20), crs="EPSG:6933", resolution=10)
    data = wrap_xr(np.full((2, 2), 3, dtype="uint8"), geobox, nodata=255)
    if chunked:
        data = data.chunk({"x": 1, "y": 1})
    # Both columns are touched; only the left column's centres are inside.
    boundary = Geometry(box(0, 0, 14, 20), crs="EPSG:6933").to_crs(4326)
    assert xarray_calculate_area_m2(data, boundary, value_list=[3]) == 400
    assert (
        xarray_calculate_area_m2(data, boundary, value_list=[3], all_touched=True)
        == 400
    )
    assert (
        xarray_calculate_area_m2(data, boundary, value_list=[3], all_touched=False)
        == 200
    )
