import geopandas as gpd
import sedona.db
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
    assert len(sample_stacgeoparquet) == 2
    assert sample_stacgeoparquet[0].id =='dep_s2_seagrass_047_017_2022'
    assert sample_stacgeoparquet[1].id =='dep_s2_seagrass_047_018_2022'

# TODO: Re-enable once we have tested this in docker
# def test_intersection_raster(
#     sample_polygon: polygon, sample_stacgeoparquet: ItemCollection
# ) -> None:
#     # sample_polygon is in EPSG:4326.
#     # This STAC-Geoparquet file contains a single item in EPSG:4326
#     # This item points to a COG that is also in EPSG:4326
#     # These will both be reprojected to EPSG:6933 for area calculation.
#     data = load_xarray_stacgeoparquet(
#         sample_stacgeoparquet, geom=sample_polygon, resolution=10, crs="epsg:6933"
#     )
#     assert data is not None

#     area = xarray_calculate_area(data, sample_polygon, "asset", 1)
#     # assert area == 19833900.0 # Old value. Updated due to better reprojection handling. 0.0343% difference to new value. New method will perform even better further from the equator (for data not already in EPSG:6933).
#     assert area == 19827100.0
#     # In QGIS I got: 19832115.15. I am happy with this difference of 0.0091%. This could be due to different reprojection methods.


def test_get_area_from_geoparquet_sedona(sample_polygon) -> None:
    sd = sedona.db.connect() 
    dataset_parquet_url = "tests/data/gmw/gmw.parquet"

    # variable = ""
    # value = ""
    # datetime_string_match = ""
    area = _get_area_from_geoparquet_sedona(
        sd,
        dataset_parquet_url,
        sample_polygon.wkt,
        # TODO: Test these params once implemented
        # variable,
        # value,
        # datetime_string_match,
    )
    assert area == 12308463893.98
