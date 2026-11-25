"""Regenerate the tiny vector fixtures used by tests/test_vector_convert.py.

Run from the repository root:  python tests/data/vector/make_fixtures.py

Three synthetic square features built in the equal-area CRS EPSG:6933, so areas
are exact by construction: "big" is a 4 ha square at the origin (the feature the
area and clipping tests measure), "east" and "north" are 1 ha squares well away from
it. Files:

* features.parquet      GeoParquet, EPSG:6933, columns name/state/kind
* features.geojson      the same features in EPSG:4326 (RFC 7946)
* features_shp.zip      zipped shapefile set (features/features.shp), EPSG:6933
* layers.gpkg           GeoPackage with layers "parks" (big, east) and
                        "forests" (north), EPSG:6933
"""

import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
from shapely.geometry import box

HERE = Path(__file__).resolve().parent


def features() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "name": ["big", "east", "north"],
            "state": ["A", "B", "A"],
            "kind": ["park", "park", "forest"],
        },
        geometry=[
            box(0, 0, 200, 200),
            box(1000, 0, 1100, 100),
            box(0, 1000, 100, 1100),
        ],
        crs="EPSG:6933",
    )


def main() -> None:
    gdf = features()
    gdf.to_parquet(HERE / "features.parquet", index=False)
    gdf.to_crs(4326).to_file(HERE / "features.geojson", driver="GeoJSON")

    with TemporaryDirectory() as directory:
        shp = Path(directory) / "features.shp"
        gdf.to_file(shp, driver="ESRI Shapefile")
        with zipfile.ZipFile(HERE / "features_shp.zip", "w") as archive:
            for part in sorted(Path(directory).iterdir()):
                archive.write(part, f"features/{part.name}")

    gpkg = HERE / "layers.gpkg"
    gpkg.unlink(missing_ok=True)
    gdf[gdf["kind"] == "park"].to_file(gpkg, layer="parks", driver="GPKG")
    gdf[gdf["kind"] == "forest"].to_file(gpkg, layer="forests", driver="GPKG")


if __name__ == "__main__":
    main()
