import os

import sedona.db
from odc.geo.geom import polygon
from pystac import ItemCollection

from csdr.products import _get_area_m2_from_geoparquet_sedona
from csdr.utils import (
    load_xarray_stacgeoparquet,
    xarray_calculate_area_m2,
)


def test_sample_polygon(sample_polygon: polygon) -> None:
    assert sample_polygon is not None
    assert sample_polygon.geom_type == "Polygon"
    assert sample_polygon.is_valid


def test_sample_stacgeoparquet(sample_stacgeoparquet: ItemCollection) -> None:
    assert sample_stacgeoparquet is not None
    assert len(sample_stacgeoparquet) == 1


def test_intersection_raster(
    sample_polygon: polygon, sample_stacgeoparquet: ItemCollection
) -> None:
    os.environ["AWS_NO_SIGN_REQUEST"] = (
        "YES"  # Needed because STAC-Geoparquet item's asset href points to our bucket that needs unsigned requests.
    )
    # sample_polygon is in EPSG:4326.
    # This STAC-Geoparquet file contains a single item in EPSG:4326
    kwargs = {"resolution": 10, "crs": "epsg:6933"}
    data = load_xarray_stacgeoparquet(sample_stacgeoparquet, **kwargs)
    assert data is not None

    # This reprojects to 6933 internally for area calculation.
    area = xarray_calculate_area_m2(data, sample_polygon, "mangrove", 1)
    assert area == 19827100.0


def test_get_area_from_geoparquet_sedona(sample_polygon: polygon) -> None:
    sd = sedona.db.connect()
    dataset_parquet_url = "tests/data/gmw/gmw.parquet"

    # variable = ""
    # value = ""
    # datetime_string_match = ""
    area = _get_area_m2_from_geoparquet_sedona(
        sd,
        dataset_parquet_url,
        sample_polygon.wkt,
        # TODO: Test these params once implemented
        # variable,
        # value,
        # datetime_string_match,
    )
    assert area == 12308463893.98
