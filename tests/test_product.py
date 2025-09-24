from odc.geo.geom import polygon
from pystac import ItemCollection

from csdr.utils import load_xarray_stacgeoparquet, xarray_calculate_area


def test_sample_polygon(sample_polygon: polygon) -> None:
    assert sample_polygon is not None
    assert sample_polygon.geom_type == "Polygon"
    assert sample_polygon.is_valid


def test_sample_stacgeoparquet(sample_stacgeoparquet: ItemCollection) -> None:
    assert sample_stacgeoparquet is not None
    assert len(sample_stacgeoparquet) == 1


def test_intersection(
    sample_polygon: polygon, sample_stacgeoparquet: ItemCollection
) -> None:
    data = load_xarray_stacgeoparquet(
        sample_stacgeoparquet, geom=sample_polygon, resolution=10, crs="epsg:8857"
    )
    assert data is not None

    area = xarray_calculate_area(data, sample_polygon, "asset", 1)
    assert area == 19833900.0
