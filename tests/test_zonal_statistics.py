"""Zonal statistics families on small grids with hand-computed expectations.

Grids are 10 m cells in EPSG:6933. Expected values are worked out by hand from
the listed cells, never by a second call to the implementation.
"""

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
import rustac
from odc.geo.geobox import GeoBox
from odc.geo.geom import Geometry
from odc.geo.xr import wrap_xr
from rasterio.transform import from_origin
from rio_stac import create_stac_item
from shapely.geometry import MultiPolygon, box
from shapely.geometry.base import BaseGeometry
from typer.testing import CliRunner
from xarray import DataArray

from csdr.cli_products import _template_process_geometry_command, products_app
from csdr.products import parse_outputs, process_indicators_for_geometry
from csdr.utils import (
    CSDRException,
    NoValidRasterCellsError,
    PercentileMethod,
    ZonalCells,
    xarray_zonal_cells,
)

ND = -9999.0
# Three rows by four columns over x 0..40, y 0..30 (row 0 is the top row).
# Columns 0-2 hold a real zero, nodata, NaN and +inf; column 3 (all 0.5) lies
# outside the standard boundary, so any leak would change every statistic.
GRID = [
    [0.0, 0.25, ND, 0.5],
    [np.nan, 0.75, 1.0, 0.5],
    [0.125, np.inf, 0.375, 0.5],
]
# Columns 0-2 under both cell rules (column 3 is neither touched nor centred).
BOUNDARY = box(0, 0, 28, 30)
# Valid cells inside BOUNDARY, sorted: six of the nine cells inside it.
VALID = [0.0, 0.125, 0.25, 0.375, 0.75, 1.0]
LINEAR = PercentileMethod()
HISTOGRAM = PercentileMethod("histogram")


def grid(
    values: list = GRID,
    dtype: str = "float32",
    nodata: float = ND,
    chunked: bool = False,
    x0: float = 0.0,
) -> DataArray:
    rows, cols = np.shape(values)
    geobox = GeoBox.from_bbox(
        (x0, 0, x0 + 10 * cols, 10 * rows), crs="EPSG:6933", resolution=10
    )
    data = wrap_xr(np.array(values, dtype=dtype), geobox, nodata=nodata)
    return data.chunk({"x": 1, "y": 2}) if chunked else data


def boundary(shape: BaseGeometry = BOUNDARY) -> Geometry:
    return Geometry(shape, crs="EPSG:6933").to_crs("EPSG:4326")


def cells(
    data: DataArray,
    shape: BaseGeometry = BOUNDARY,
    method: PercentileMethod = LINEAR,
    **kwargs: Any,  # noqa: ANN401
) -> ZonalCells:
    return xarray_zonal_cells(
        data,
        boundary(shape),
        collect_values=not method.is_histogram,
        histogram=method if method.is_histogram else None,
        collect_distinct=True,
        **kwargs,
    )


@pytest.mark.parametrize("chunked", [False, True])
def test_counts_mean_and_valid_fraction(chunked: bool) -> None:
    result = cells(grid(chunked=chunked))
    assert result.boundary_cells == 9  # nodata, NaN and inf cells included
    assert result.valid_cells == 6
    assert result.mean() == 2.5 / 6  # float64 sum of the six values / 6
    assert result.valid_fraction() == 6 / 9


@pytest.mark.parametrize("chunked", [False, True])
def test_linear_percentiles_interpolate_between_valid_values(chunked: bool) -> None:
    result = cells(grid(chunked=chunked))
    # Position q * (N - 1) over the sorted valid values (N = 6).
    assert result.percentile(0.1, LINEAR) == 0.0625  # halfway 0.0 -> 0.125
    assert result.percentile(0.25, LINEAR) == 0.15625  # h = 1.25: 0.125 + 0.25 * 0.125
    assert result.percentile(0.5, LINEAR) == 0.3125  # halfway 0.25 -> 0.375
    assert result.percentile(0.75, LINEAR) == 0.65625  # h = 3.75: 0.375 + 0.75 * 0.375
    assert result.percentile(0.9, LINEAR) == 0.875  # halfway 0.75 -> 1.0


@pytest.mark.parametrize("chunked", [False, True])
def test_histogram_percentiles_are_bin_midpoints(chunked: bool) -> None:
    result = cells(grid(chunked=chunked), method=HISTOGRAM)
    # 1000 bins of 0.001 over [0, 1]; 1.0 falls into the last bin (999).
    # The ceil(q * N)-th smallest valid value picks the bin.
    assert result.percentile(0.1, HISTOGRAM) == pytest.approx(0.0005)  # 1st: 0.0
    assert result.percentile(0.25, HISTOGRAM) == pytest.approx(0.1255)  # 2nd: 0.125
    assert result.percentile(0.5, HISTOGRAM) == pytest.approx(0.2505)  # 3rd: 0.25
    assert result.percentile(0.75, HISTOGRAM) == pytest.approx(0.7505)  # 5th: 0.75
    assert result.percentile(0.9, HISTOGRAM) == pytest.approx(0.9995)  # 6th: 1.0
    assert result.histogram_clipped_cells == 0


def test_histogram_with_coarse_bins_and_out_of_range_values() -> None:
    four = PercentileMethod("histogram", bins=4, value_range=(0.0, 1.0))
    result = cells(grid(), method=four)
    # Bins of 0.25: [0, 0.125] -> 0, [0.25, 0.375] -> 1, [0.75, 1.0] -> 3.
    assert result.histogram[:4].tolist() == [2, 2, 0, 2]
    assert result.percentile(0.5, four) == 0.375  # bin 1 holds the 3rd value
    half = PercentileMethod("histogram", bins=4, value_range=(0.0, 0.5))
    clipped = cells(grid(), method=half)
    # 0.75 and 1.0 lie above 0.5 and are counted in the last bin.
    assert clipped.histogram_clipped_cells == 2
    assert clipped.histogram[:4].tolist() == [1, 1, 1, 3]


def test_histogram_quantile_on_an_exact_multiple_takes_the_qn_th_value() -> None:
    # 30 valid cells 0.0002..0.0292, one per 0.001 bin. q * N = 3 for p10: the
    # first bin whose cumulative count reaches 3 is bin 2 (the 3rd value), not
    # bin 3; q * N = 15 for p50: bin 14.
    values = [[i / 1000 + 0.0002 for i in range(30)]]
    result = xarray_zonal_cells(
        grid(values, dtype="float64"),
        boundary(box(0, 0, 300, 10)),
        all_touched=False,
        histogram=HISTOGRAM,
    )
    assert result.valid_cells == 30
    assert result.percentile(0.1, HISTOGRAM) == pytest.approx(0.0025)
    assert result.percentile(0.5, HISTOGRAM) == pytest.approx(0.0145)


@pytest.mark.parametrize("chunked", [False, True])
def test_distinct_count_over_valid_cells_and_restricted_to_classes(
    chunked: bool,
) -> None:
    classes = [[0, 3, 65535, 9], [3, 5, 0, 9], [7, 5, 3, 9]]
    data = grid(classes, dtype="uint16", nodata=65535, chunked=chunked)
    result = cells(data)
    # Inside columns 0-2: {0, 3, 5, 7}; 65535 is nodata; 9 is outside.
    assert result.distinct_count() == 4
    assert result.distinct_count([3.0, 5.0, 9.0, 11.0]) == 2  # 0 and 7 excluded
    assert result.distinct_count([11.0]) == 0  # valid cells exist: a real zero


def test_chunked_equals_eager_for_every_statistic() -> None:
    eager = cells(grid())
    lazy = cells(grid(chunked=True))
    for name in ("boundary_cells", "valid_cells", "valid_sum"):
        assert getattr(lazy, name) == getattr(eager, name)
    assert lazy.percentile(0.5, LINEAR) == eager.percentile(0.5, LINEAR)
    assert sorted(np.concatenate(lazy.values)) == VALID
    eager_h = cells(grid(), method=HISTOGRAM)
    lazy_h = cells(grid(chunked=True), method=HISTOGRAM)
    assert np.array_equal(lazy_h.histogram, eager_h.histogram)


@pytest.mark.parametrize("chunked", [False, True])
def test_cell_centre_and_all_touched_differ_on_a_boundary_crossing_a_column(
    chunked: bool,
) -> None:
    data = grid(chunked=chunked)
    crossing = box(0, 0, 14, 30)  # crosses column 1; its centres (x=15) are outside
    centre = cells(data, crossing, all_touched=False)
    touched = cells(data, crossing, all_touched=True)
    # Cell centre: column 0 only = 0.0, NaN, 0.125.
    assert (centre.boundary_cells, centre.valid_cells) == (3, 2)
    assert centre.mean() == 0.0625
    assert centre.valid_fraction() == 2 / 3
    # All touched: columns 0 and 1 = 0.0, 0.25, NaN, 0.75, 0.125, inf.
    assert (touched.boundary_cells, touched.valid_cells) == (6, 4)
    assert touched.mean() == 1.125 / 4
    assert touched.valid_fraction() == 4 / 6


def test_cells_outside_the_source_extent_are_not_in_the_denominator() -> None:
    # Columns 4-5 are fill (outside the source raster, x 0..40), same as nodata.
    wide = [[*row, ND, ND] for row in GRID]
    data = grid(wide)
    whole = box(0, 0, 60, 30)
    extent = Geometry(box(0, 0, 40, 30), crs="EPSG:6933")
    with_extent = cells(data, whole, all_touched=False, extent=extent)
    without = cells(data, whole, all_touched=False)
    assert with_extent.valid_cells == without.valid_cells == 9  # six + column 3
    assert with_extent.boundary_cells == 12
    assert without.boundary_cells == 18
    assert with_extent.valid_fraction() == 9 / 12


@pytest.mark.parametrize("chunked", [False, True])
def test_no_valid_cell_inside_the_boundary_raises(chunked: bool) -> None:
    # Valid cells exist only in column 3, outside the boundary.
    values = [[ND, np.nan, np.inf, 0.5]] * 3
    result = cells(grid(values, chunked=chunked))
    assert result.boundary_cells == 9 and result.valid_cells == 0
    for statistic in (
        result.mean,
        result.valid_fraction,
        result.distinct_count,
        lambda: result.percentile(0.5, LINEAR),
    ):
        with pytest.raises(NoValidRasterCellsError, match="cannot be reported as zero"):
            statistic()


def test_more_than_one_time_slice_is_refused() -> None:
    data = grid().expand_dims(time=2)
    with pytest.raises(CSDRException, match="exactly one time slice"):
        cells(data)
    assert cells(grid().expand_dims(time=1)).valid_cells == 6


@pytest.mark.parametrize(
    ("method", "range_text", "message"),
    [
        ("nearest", "0,1", "Unknown percentile method"),
        ("histogram", "1,0", "lo < hi"),
        ("histogram", "0,nan", "lo < hi"),
    ],
)
def test_percentile_method_validation(
    method: str, range_text: str, message: str
) -> None:
    with pytest.raises(CSDRException, match=message):
        PercentileMethod(method, 10, PercentileMethod.parse_range(range_text))
    with pytest.raises(CSDRException, match="two numbers"):
        PercentileMethod.parse_range("0")
    assert PercentileMethod("histogram", 50, (0.0, 2.0)).metadata() == {
        "percentileMethod": "histogram",
        "percentileBins": 50,
        "percentileRange": [0.0, 2.0],
    }
    assert LINEAR.metadata() == {"percentileMethod": "linear"}


# --- process_indicators_for_geometry: dispatch, multipart union, ordering ---

STATS = {
    "mean-index": {"indicator-name": "index"},
    "p10-index": {"indicator-name": "index"},
    "p25-index": {"indicator-name": "index"},
    "p50-index": {"indicator-name": "index"},
    "p75-index": {"indicator-name": "index"},
    "p90-index": {"indicator-name": "index"},
    "valid-fraction-index": {"indicator-name": "index"},
}


def run(
    geometry: Geometry,
    indicators: dict,
    data_for_part: Callable[[Geometry], DataArray | None],
    *,
    method: PercentileMethod = LINEAR,
    all_touched: bool = False,
    area: Callable[..., float] | None = None,
    metadata: dict | None = None,
    data_type: str = "stac-geoparquet",
) -> tuple[dict, list]:
    """Run the geometry with each part's cells taken from ``data_for_part(part)``."""
    calls = []

    def zonal(
        url: str,
        kind: str,
        part: Geometry,
        indicator: str,
        **kwargs: Any,  # noqa: ANN401
    ) -> ZonalCells | None:
        calls.append((indicator, kwargs))
        data = data_for_part(part)
        if data is None:
            return None  # no STAC item covers this part
        kwargs = {
            k: v
            for k, v in kwargs.items()
            if k not in {"datetime_string_match", "load_kwargs"}
        }
        all_touched = kwargs.pop("raster_all_touched")
        return xarray_zonal_cells(data, part, all_touched=all_touched, **kwargs)

    with (
        patch("csdr.products.sedona.db.connect"),
        patch(
            "csdr.products.read_provenance",
            return_value={"dataType": data_type, "dataUrl": "fixture.parquet"},
        ),
        patch(
            "csdr.products._get_zonal_cells_from_dataset_geometry", side_effect=zonal
        ),
        patch(
            "csdr.products._get_area_m2_from_dataset_geometry",
            side_effect=area or (lambda *a, **k: pytest.fail("no area expected")),
        ),
    ):
        result = process_indicators_for_geometry(
            geometry,
            indicators,
            "fixture-provenance.json",
            raster_all_touched=all_touched,
            percentile_method=method,
            zonal_metadata=metadata,
        )
    return result, calls


def test_every_statistic_of_a_band_comes_from_one_pass() -> None:
    metadata = {}
    result, calls = run(boundary(), STATS, lambda part: grid(), metadata=metadata)
    assert len(calls) == 1  # seven indicators, one band, one part: one pass
    assert calls[0][1]["collect_values"] is True
    assert calls[0][1]["histogram"] is None
    assert result == {
        "mean-index": 2.5 / 6,
        "p10-index": 0.0625,
        "p25-index": 0.15625,
        "p50-index": 0.3125,
        "p75-index": 0.65625,
        "p90-index": 0.875,
        "valid-fraction-index": 6 / 9,
    }
    assert metadata == {
        "rasterCellRule": "cell-centre",
        "zonalCellCounts": {"index": {"boundaryCells": 9, "validCells": 6}},
        "percentileMethod": "linear",
    }


def test_histogram_method_is_used_and_recorded() -> None:
    metadata = {}
    result, calls = run(
        boundary(), STATS, lambda part: grid(), method=HISTOGRAM, metadata=metadata
    )
    assert calls[0][1]["collect_values"] is False
    assert calls[0][1]["histogram"] == HISTOGRAM
    assert result["p10-index"] == pytest.approx(0.0005)
    assert result["p25-index"] == pytest.approx(0.1255)
    assert result["p50-index"] == pytest.approx(0.2505)
    assert result["p75-index"] == pytest.approx(0.7505)
    assert result["p90-index"] == pytest.approx(0.9995)
    assert result["mean-index"] == 2.5 / 6  # the mean never uses the histogram
    assert metadata["percentileMethod"] == "histogram"
    assert metadata["percentileBins"] == 1000
    assert metadata["percentileRange"] == [0.0, 1.0]
    assert metadata["zonalCellCounts"]["index"]["histogramClippedCells"] == 0


def test_metadata_has_no_percentile_keys_without_percentiles() -> None:
    metadata = {}
    run(
        boundary(),
        {"mean-index": {"indicator-name": "index"}},
        lambda p: grid(),
        metadata=metadata,
    )
    assert "percentileMethod" not in metadata
    assert metadata["zonalCellCounts"] == {
        "index": {"boundaryCells": 9, "validCells": 6}
    }


# Two disjoint parts, each over its own 2x2 grid: part A (x 0..20) holds
# 0.0, 0.125, nodata, NaN; part B (x 40..60) holds 1.0, nodata, nodata, nodata.
PART_A = box(0, 0, 20, 20)
PART_B = box(40, 0, 60, 20)
GRID_A = [[0.0, 0.125], [ND, np.nan]]
GRID_B = [[1.0, ND], [ND, ND]]


def two_parts(part: Geometry) -> DataArray:
    centre_x = part.to_crs("EPSG:6933").centroid.coords[0][0]
    return grid(GRID_A) if centre_x < 30 else grid(GRID_B, x0=40.0)


@pytest.mark.parametrize("method", [LINEAR, HISTOGRAM])
def test_multipart_statistics_are_over_the_union_of_parts(
    method: PercentileMethod,
) -> None:
    geometry = boundary(MultiPolygon([PART_A, PART_B]))
    metadata = {}
    result, calls = run(geometry, STATS, two_parts, method=method, metadata=metadata)
    assert len(calls) == 2  # one pass per part, combined once
    # Union: valid 0.0, 0.125, 1.0 of 8 cells. A mean of part means would be
    # (0.0625 + 1.0) / 2 = 0.53125; a mean of part fractions (2/4 + 1/4) / 2.
    assert result["mean-index"] == 1.125 / 3
    assert result["valid-fraction-index"] == 3 / 8
    assert metadata["zonalCellCounts"]["index"] == {
        "boundaryCells": 8,
        "validCells": 3,
        **({"histogramClippedCells": 0} if method.is_histogram else {}),
    }
    if method.is_histogram:
        assert result["p25-index"] == pytest.approx(0.0005)  # 1st of 3: 0.0
        assert result["p50-index"] == pytest.approx(0.1255)  # 2nd of 3: 0.125
        assert result["p75-index"] == pytest.approx(0.9995)  # 3rd of 3: 1.0
        assert result["p90-index"] == pytest.approx(0.9995)  # 3rd of 3: 1.0
    else:
        assert result["p25-index"] == 0.0625  # h = 0.5: halfway 0.0 -> 0.125
        assert result["p50-index"] == 0.125  # middle of 0.0, 0.125, 1.0
        assert result["p75-index"] == 0.5625  # h = 1.5: halfway 0.125 -> 1.0
        assert result["p90-index"] == pytest.approx(0.825)  # 0.125 + 0.8 * 0.875


def test_distinct_count_over_the_union_of_parts() -> None:
    geometry = boundary(MultiPolygon([PART_A, PART_B]))

    def classes(part: Geometry) -> DataArray:
        centre_x = part.to_crs("EPSG:6933").centroid.coords[0][0]
        if centre_x < 30:
            return grid([[3, 5], [5, 65535]], dtype="uint16", nodata=65535)
        return grid([[5, 7], [0, 65535]], dtype="uint16", nodata=65535, x0=40.0)

    result, _ = run(
        geometry,
        {
            "distinct-class-count": {
                "indicator-name": "classes",
                "indicator-value": "3,5,7",
            },
            "distinct-any-count": {"indicator-name": "classes"},
        },
        classes,
    )
    # Union {0, 3, 5, 7}: 5 is in both parts and counted once.
    assert result == {"distinct-any-count": 4, "distinct-class-count": 3}


def test_a_part_with_no_valid_cells_still_counts_its_cells() -> None:
    geometry = boundary(MultiPolygon([PART_A, PART_B]))

    def part_b_all_nodata(part: Geometry) -> DataArray:
        data = two_parts(part)
        return grid([[ND, ND], [ND, ND]], x0=40.0) if data.x.values[0] > 30 else data

    result, _ = run(geometry, STATS, part_b_all_nodata)
    assert result["valid-fraction-index"] == 2 / 8
    assert result["mean-index"] == 0.0625


def test_a_part_outside_every_source_raster_adds_no_cells() -> None:
    geometry = boundary(MultiPolygon([PART_A, PART_B]))

    def only_part_a(part: Geometry) -> DataArray | None:
        data = two_parts(part)
        return None if data.x.values[0] > 30 else data

    result, _ = run(geometry, STATS, only_part_a)
    assert result["valid-fraction-index"] == 2 / 4
    assert result["mean-index"] == 0.0625


def test_no_stac_coverage_for_any_part_raises() -> None:
    with pytest.raises(CSDRException, match="No STAC items cover"):
        run(boundary(MultiPolygon([PART_A, PART_B])), STATS, lambda part: None)


def test_no_valid_cells_anywhere_in_the_boundary_raises() -> None:
    nodata = [[ND, np.nan], [np.inf, ND]]
    with pytest.raises(NoValidRasterCellsError, match="cannot be reported as zero"):
        run(boundary(PART_A), STATS, lambda part: grid(nodata))


def test_percent_still_runs_after_sum_alongside_statistics() -> None:
    seen = []

    def area(*args: object, **kwargs: object) -> float:
        seen.append(kwargs["indicator"])
        return 450.0

    square = boundary(box(0, 0, 30, 30))  # 900 m²
    result, calls = run(
        square,
        {
            "percent-wet-area": {"indicator-name": "wet"},
            "mean-index": {"indicator-name": "index"},
            "sum-wet-area": {"indicator-name": "wet", "indicator-value": 1},
        },
        lambda part: grid(),
        area=area,
    )
    assert seen == ["wet"]
    assert len(calls) == 1
    assert result["sum-wet-area"] == 450.0
    assert result["percent-wet-area"] == pytest.approx(50.0, rel=1e-6)
    assert result["mean-index"] == 2.5 / 6


@pytest.mark.parametrize(
    ("indicators", "message"),
    [
        (
            {"mean-index": {"indicator-name": "index", "indicator-value": 1}},
            "indicator-value applies only to distinct",
        ),
        (
            {
                "distinct-class-count": {
                    "indicator-name": "classes",
                    "indicator-value": "3,a",
                }
            },
            "comma-separated list of numbers",
        ),
        ({"p50-index": {"indicator-name": None}}, "needs an indicator-name"),
    ],
)
def test_bad_statistic_requests_are_refused_before_any_source_is_read(
    indicators: dict, message: str
) -> None:
    with (
        patch(
            "csdr.products.read_provenance",
            side_effect=AssertionError("no source may be read"),
        ),
        pytest.raises(CSDRException, match=message),
    ):
        process_indicators_for_geometry(boundary(), indicators, "unused")


def test_statistics_need_a_raster_dataset() -> None:
    with pytest.raises(CSDRException, match="need a 'stac-geoparquet' raster dataset"):
        run(boundary(), STATS, lambda part: grid(), data_type="geoparquet")


# --- End to end: GeoTIFF -> STAC item -> STAC-GeoParquet -> process-geometry ---


@pytest.fixture
def raster_dataset(tmp_path: Path) -> dict[str, str]:
    """A 3x4 float32 COG-like GeoTIFF indexed as STAC-GeoParquet (x 0..40, y 0..30)."""
    return make_raster_dataset(tmp_path, with_proj=True)


def make_raster_dataset(tmp_path: Path, *, with_proj: bool) -> dict[str, str]:
    tif = tmp_path / "index.tif"
    with rasterio.open(
        tif,
        "w",
        driver="GTiff",
        width=4,
        height=3,
        count=1,
        dtype="float32",
        crs="EPSG:6933",
        transform=from_origin(0, 30, 10, 10),
        nodata=ND,
    ) as dst:
        dst.write(np.array(GRID, dtype="float32"), 1)
    item = create_stac_item(
        str(tif),
        input_datetime=dt.datetime(2023, 1, 1, tzinfo=dt.UTC),
        asset_name="index",
        with_proj=with_proj,
        with_raster=True,
        id="index-2023",
    )
    stac = tmp_path / "index.parquet"
    rustac.write_sync(str(stac), [item.to_dict()])
    dataset = tmp_path / "index.parquet.provenance.json"
    dataset.write_text(
        json.dumps({"dataUrl": str(stac), "dataType": "stac-geoparquet"})
    )

    # Boundaries: columns 0-2 inside the raster; and one reaching 20 m past the
    # raster's right and top edges (loaded there as fill, outside the extent).
    boundaries = gpd.GeoDataFrame(
        {"csdr-id": ["inside", "overhang"], "csdr-name": ["Inside", "Overhang"]},
        geometry=[BOUNDARY, box(0, 0, 60, 50)],
        crs="EPSG:6933",
    )
    boundaries.to_parquet(tmp_path / "boundaries.parquet")
    geometry = tmp_path / "boundaries.parquet.provenance.json"
    geometry.write_text(json.dumps({"dataUrl": str(tmp_path / "boundaries.parquet")}))
    return {"dataset": str(dataset), "geometry": str(geometry)}


def process(
    raster_dataset: dict[str, str], target: Path, geometry_id: str, *extra: str
) -> dict:
    result = CliRunner().invoke(
        products_app,
        [
            "process-geometry",
            "--product-id=p",
            "--run-id=r",
            f"--geometry-provenance-url={raster_dataset['geometry']}",
            f"--dataset-provenance-url={raster_dataset['dataset']}",
            "--indicators-to-extract",
            json.dumps(STATS),
            "--datetime=2023",
            f"--target-location={target}",
            f"--geometry-id={geometry_id}",
            *extra,
        ],
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output
    # Several indicators share one output folder.
    path = target / "many-indicators" / "2023" / f"p-{geometry_id}.json"
    return json.loads(path.read_text())


@pytest.mark.parametrize("cell_rule", ["--raster-cell-centre", "--raster-all-touched"])
def test_process_geometry_end_to_end(
    raster_dataset: dict[str, str], tmp_path: Path, cell_rule: str
) -> None:
    output = process(raster_dataset, tmp_path / "out", "inside", cell_rule)
    assert output["indicators"] == {
        "mean-index": 2.5 / 6,
        "p10-index": 0.0625,
        "p25-index": 0.15625,
        "p50-index": 0.3125,
        "p75-index": 0.65625,
        "p90-index": 0.875,
        "valid-fraction-index": 6 / 9,
    }
    metadata = output["metadata"]
    assert metadata["percentileMethod"] == "linear"
    assert metadata["zonalCellCounts"] == {
        "index": {"boundaryCells": 9, "validCells": 6, "extentSource": "proj"}
    }
    assert metadata["rasterCellRule"] == cell_rule.removeprefix("--raster-")


def test_process_geometry_histogram_and_source_extent(
    raster_dataset: dict[str, str], tmp_path: Path
) -> None:
    output = process(
        raster_dataset,
        tmp_path / "out",
        "overhang",
        "--raster-cell-centre",
        "--percentile-method=histogram",
        "--percentile-bins=4",
        "--percentile-range=0,2",
    )
    # All 12 raster cells are inside; the 5x6 window loaded around the boundary
    # adds 18 fill cells outside the raster extent, which are not counted.
    assert output["metadata"]["zonalCellCounts"] == {
        "index": {
            "boundaryCells": 12,
            "validCells": 9,
            "histogramClippedCells": 0,
            "extentSource": "proj",
        }
    }
    assert output["indicators"]["valid-fraction-index"] == 9 / 12
    assert output["indicators"]["mean-index"] == (2.5 + 1.5) / 9
    # Bins of 0.5 over [0, 2]: 0, 0.125, 0.25, 0.375 -> bin 0; three 0.5 and
    # 0.75 -> bin 1; 1.0 -> bin 2. p10 (target 0.9) is bin 0, p50 (4.5) bin 1.
    assert output["indicators"]["p10-index"] == 0.25
    assert output["indicators"]["p50-index"] == 0.75
    assert output["indicators"]["p25-index"] == 0.25  # target 2.25: bin 0
    assert output["indicators"]["p75-index"] == 0.75  # target 6.75: bin 1
    assert output["metadata"]["percentileMethod"] == "histogram"
    assert output["metadata"]["percentileBins"] == 4
    assert output["metadata"]["percentileRange"] == [0.0, 2.0]


def test_statistics_and_method_metadata_survive_consolidation(
    raster_dataset: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CSDR_PROVENANCE_STEPS_DIR", str(tmp_path / "steps"))
    monkeypatch.setenv("IMAGE_REPO", "https://github.com/example/toolkit/tree/x/")
    target = tmp_path / "out"
    flags = ("--raster-cell-centre", "--percentile-method=histogram")
    for geometry_id in ("inside", "overhang"):
        process(raster_dataset, target, geometry_id, *flags)
    result = CliRunner().invoke(
        products_app,
        [
            "consolidate",
            "--product-id=p",
            f"--location={target}",
            f"--geometry-provenance-url={raster_dataset['geometry']}",
            f"--dataset-provenance-url={raster_dataset['dataset']}",
            "--indicator-name=many-indicators",
        ],
    )
    assert result.exit_code == 0, result.output
    table = pd.read_parquet(target / "many-indicators" / "p.parquet")
    outputs = parse_outputs(table)
    rows = {
        row["geometryOutputId"]: row
        for row in outputs["mean-index"]["2023-01-01T00:00:00Z"]
    }
    assert rows["inside"]["value"] == 2.5 / 6
    assert rows["overhang"]["value"] == 4.0 / 9
    metadata = rows["overhang"]["metadata"]
    assert metadata["percentileMethod"] == "histogram"
    assert metadata["percentileBins"] == 1000
    assert metadata["percentileRange"] == [0.0, 1.0]
    assert metadata["rasterCellRule"] == "cell-centre"
    assert metadata["zonalCellCounts"]["index"] == {
        "boundaryCells": 12,
        "validCells": 9,
        "histogramClippedCells": 0,
        "extentSource": "proj",
    }


def test_footprint_extent_is_recorded_and_warned(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Without STAC projection fields the extent falls back to the item footprint
    # (here it coincides with the raster: EPSG:6933 is cylindrical), and the
    # grid comes from --load-kwargs.
    dataset = make_raster_dataset(tmp_path, with_proj=False)
    with caplog.at_level("WARNING", logger="csdr.products"):
        output = process(
            dataset,
            tmp_path / "out",
            "overhang",
            "--raster-cell-centre",
            '--load-kwargs={"crs": "EPSG:6933", "resolution": 10}',
        )
    assert output["metadata"]["zonalCellCounts"] == {
        "index": {"boundaryCells": 12, "validCells": 9, "extentSource": "footprint"}
    }
    assert "lack projection fields" in caplog.text


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        (["proj", "proj"], "proj"),
        (["proj", "footprint"], "footprint"),
        (["footprint", "none"], "none"),
        ([None, "proj"], "proj"),
    ],
)
def test_merged_parts_keep_the_weakest_extent_source(
    sources: list[str | None], expected: str
) -> None:
    combined = ZonalCells()
    for source in sources:
        combined.merge(ZonalCells(valid_cells=1, extent_source=source))
    assert combined.cell_counts()["extentSource"] == expected


PG = "csdr products process-geometry"


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            "--geometry-id=1 --percentile-bins=1000 --datetime=2023",
            "--geometry-id={geometry_id} --percentile-bins=1000 --datetime={datetime}",
        ),
        (
            "--geometry-id 1 --percentile-bins 1000",
            "--geometry-id {geometry_id} --percentile-bins 1000",
        ),
        (
            "--geometry-ids=1,2,3 --percentile-bins=1000",
            "--geometry-ids={geometry_ids} --percentile-bins=1000",
        ),
        (
            "--geometry-ids 1,2 --percentile-bins 100",
            "--geometry-ids {geometry_ids} --percentile-bins 100",
        ),
    ],
)
def test_consolidate_templates_only_the_geometry_id_values(
    args: str, expected: str
) -> None:
    command = f"{PG} {args}"
    assert _template_process_geometry_command(command, "2023") == f"{PG} {expected}"


def test_consolidate_step_keeps_numeric_option_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = []
    monkeypatch.setattr(
        "csdr.cli_products.write_step", lambda **kwargs: steps.append(kwargs)
    )
    folder = tmp_path / "many-indicators" / "2023"
    folder.mkdir(parents=True)
    command = (
        "csdr products process-geometry --geometry-id=1 --raster-cell-centre "
        "--percentile-method=histogram --percentile-bins=1000 --datetime=2023"
    )
    (folder / "p-1.json").write_text(
        json.dumps(
            {
                "id": "x",
                "productId": "p",
                "productRunId": "r",
                "geometryOutputId": "1",
                "timePoint": "2023-01-01T00:00:00Z",
                "indicators": {"p50-index": 0.2505},
                "metadata": {
                    "geometryProvenanceUrl": "g.json",
                    "datasetProvenanceUrl": "d.json",
                    "command": command,
                },
            }
        )
    )
    result = CliRunner().invoke(
        products_app,
        [
            "consolidate",
            "--product-id=p",
            f"--location={tmp_path}",
            "--geometry-provenance-url=g.json",
            "--dataset-provenance-url=d.json",
            "--indicator-name=many-indicators",
        ],
    )
    assert result.exit_code == 0, result.output
    assert steps[0]["command"] == (
        "csdr products process-geometry --geometry-id={geometry_id} "
        "--raster-cell-centre --percentile-method=histogram --percentile-bins=1000 "
        "--datetime={datetime}"
    )
