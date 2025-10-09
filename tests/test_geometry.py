from geopandas import GeoDataFrame

from csdr.geometries import convert_gdf_row_to_geometry_output


def test_convert_gdf_row_to_geometry_output(sample_gdf: GeoDataFrame) -> None:
    assert len(sample_gdf) == 1

    geometry_output = convert_gdf_row_to_geometry_output(
        sample_gdf.iloc[0], sample_gdf.crs
    )

    assert "geometry" in geometry_output
    assert "name" in geometry_output
