import geopandas as gpd
from odc.geo.geom import polygon
from pystac import ItemCollection

from csdr.products import _get_area_from_geoparquet_sedona
from csdr.utils import (
    load_xarray_stacgeoparquet,
    xarray_calculate_area,
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
    # sample_polygon is in EPSG:4326.
    # This STAC-Geoparquet file contains a single item in EPSG:4326
    # This item points to a COG that is also in EPSG:4326
    # These will both be reprojected to EPSG:6933 for area calculation.
    data = load_xarray_stacgeoparquet(
        sample_stacgeoparquet, geom=sample_polygon, resolution=10, crs="epsg:6933"
    )
    assert data is not None

    area = xarray_calculate_area(data, sample_polygon, "asset", 1)
    # assert area == 19833900.0 # Old value. Updated due to better reprojection handling. 0.0343% difference to new value. New method will perform even better further from the equator (for data not already in EPSG:6933).
    assert area == 19827100.0
    # In QGIS I got: 19832115.15. I am happy with this difference of 0.0091%. This could be due to different reprojection methods.


# TODO: Update this to test _get_area_from_geoparquet_sedona
# def test__get_area_from_geoparquet_sedona() -> None:
#     # CRS stuff is handled inside the function.
#     dataset_parquet_url = "tests/data/gmw/gmw.parquet"
#     geometry_wkt = (
      # Nauru bbox roughly:
#       "POLYGON ((165.52158873158862 0.7565336916904357, 165.52158873158862 -2.2879479496098583, 168.5549585762643 -2.2879479496098583, 168.5549585762643 0.7565336916904357, 165.52158873158862 0.7565336916904357))"
#       )
#     variable = ""
#     value = ""
#     datetime_string_match = ""
#     area = _get_area_from_geoparquet_sedona(
#         dataset_parquet_url,
#         geometry_wkt,
#         variable,
#         value,
#         datetime_string_match,
#     )
#     assert area == 99.99  # Placeholder value
