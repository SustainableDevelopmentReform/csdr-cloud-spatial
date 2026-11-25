"""Generic vector conversion (csdr convert vector-to-geoparquet) and zip-to-parquet."""

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
import sedona.db
from obstore.store import LocalStore
from odc.geo.geom import Geometry
from shapely.geometry import Polygon, box
from typer.testing import CliRunner

from csdr.cli_conversion import conversion_app
from csdr.cli_products import products_app
from csdr.products import _get_area_m2_from_dataset_geometry
from csdr.utils import CSDRException

VECTOR = Path(__file__).parent / "data" / "vector"
runner = CliRunner()


@pytest.fixture(autouse=True)
def steps_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "steps"
    monkeypatch.setenv("CSDR_PROVENANCE_STEPS_DIR", str(directory))
    monkeypatch.setenv(
        "IMAGE_REPO", "https://github.com/example/toolkit/tree/fixture-commit/"
    )
    return directory


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def last_step(steps_dir: Path) -> dict:
    return json.loads(max(steps_dir.glob("step-*.json")).read_text())


def convert(*args: str) -> None:
    result = runner.invoke(conversion_app, ["vector-to-geoparquet", *args])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "source,extra,rows,epsg",
    [
        ("features.parquet", [], 3, 6933),
        ("features.geojson", [], 3, 4326),
        ("features_shp.zip", [], 3, 6933),
        ("layers.gpkg", ["--layer", "parks"], 2, 6933),
    ],
)
def test_each_format_converts_and_records_its_source(
    tmp_path: Path,
    steps_dir: Path,
    source: str,
    extra: list[str],
    rows: int,
    epsg: int,
) -> None:
    out = tmp_path / "out"
    convert(
        "--source",
        str(VECTOR / source),
        "--target-location",
        str(out),
        "--target-filename",
        "ds",
        *extra,
    )
    gdf = gpd.read_parquet(out / "ds.parquet")
    assert len(gdf) == rows
    assert gdf.crs.to_epsg() == epsg  # native CRS preserved
    assert gdf.geometry.name == "geometry"
    assert not (out / "partition").exists()
    step = last_step(steps_dir)
    assert step["inputs"]["source"] == str(VECTOR / source)
    assert step["inputs"]["source_sha256"] == sha256(VECTOR / source)
    assert step["outputs"] == {
        "target_url": f"{out}/ds.parquet",
        "row_count": rows,
        "crs": f"EPSG:{epsg}",
        "dropped_empty_geometries": 0,
    }
    assert step["label"] == "Convert vector source to GeoParquet"


def test_default_target_name_comes_from_the_source_or_zip_member(
    tmp_path: Path,
) -> None:
    convert(
        "--source", str(VECTOR / "features_shp.zip"), "--target-location", str(tmp_path)
    )
    assert (tmp_path / "features.parquet").exists()


@pytest.mark.parametrize(
    "source,engine",
    [
        ("features_shp.zip", "ogr-sql"),
        ("features.geojson", "ogr-sql"),
        ("features.parquet", "sedonadb-sql"),
    ],
)
def test_where_filters_at_read_time_and_is_recorded(
    tmp_path: Path, steps_dir: Path, source: str, engine: str
) -> None:
    convert(
        "--source",
        str(VECTOR / source),
        "--target-location",
        str(tmp_path),
        "--target-filename",
        "state-a",
        "--where",
        "state = 'A'",
    )
    gdf = gpd.read_parquet(tmp_path / "state-a.parquet")
    assert sorted(gdf["name"]) == ["big", "north"]
    inputs = last_step(steps_dir)["inputs"]
    assert inputs["where"] == "state = 'A'"
    assert inputs["where_engine"] == engine


def test_empty_selection_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CSDRException, match="selection is empty"):
        convert(
            "--source",
            str(VECTOR / "features.geojson"),
            "--target-location",
            str(tmp_path),
            "--where",
            "state = 'NONE'",
        )
    assert not list(tmp_path.glob("*.parquet"))


def test_keep_columns_selects_attributes_and_refuses_unknown_ones(
    tmp_path: Path,
) -> None:
    convert(
        "--source",
        str(VECTOR / "features.parquet"),
        "--target-location",
        str(tmp_path),
        "--keep-columns",
        "state",
    )
    assert list(gpd.read_parquet(tmp_path / "features.parquet").columns) == [
        "state",
        "geometry",
    ]
    with pytest.raises(CSDRException, match=r"Columns not in the source: \['nope'\]"):
        convert(
            "--source",
            str(VECTOR / "features.parquet"),
            "--target-location",
            str(tmp_path / "x"),
            "--keep-columns",
            "state,nope",
        )


def test_multi_layer_source_needs_a_layer(tmp_path: Path) -> None:
    with pytest.raises(CSDRException, match="several layers"):
        convert(
            "--source", str(VECTOR / "layers.gpkg"), "--target-location", str(tmp_path)
        )
    with pytest.raises(CSDRException, match="Layer 'lakes' not found"):
        convert(
            "--source",
            str(VECTOR / "layers.gpkg"),
            "--target-location",
            str(tmp_path),
            "--layer",
            "lakes",
        )


def test_partitions_are_epsg4326_and_read_by_the_product_engine(
    tmp_path: Path, steps_dir: Path
) -> None:
    out = tmp_path / "dataset"
    convert(
        "--source",
        str(VECTOR / "features.parquet"),
        "--target-location",
        str(out),
        "--target-filename",
        "features",
        "--partition",
        "--grid-chunks",
        "40",
    )
    files = sorted((out / "partition").glob("features_*.parquet"))
    assert files
    assert all(gpd.read_parquet(f).crs.to_epsg() == 4326 for f in files)
    assert gpd.read_parquet(out / "features.parquet").crs.to_epsg() == 6933
    outputs = last_step(steps_dir)["outputs"]
    assert outputs["partition_count"] == len(files)
    assert outputs["partition_prefix"] == f"{out}/partition/"

    # SedonaDB reads the local prefix with the options products.py passes.
    sd = sedona.db.connect()
    sd.read_parquet(
        f"{out}/partition/",
        options={"aws.skip_signature": True, "aws.region": "ap-southeast-2"},
    ).to_view("p", overwrite=True)
    assert sd.sql("SELECT COUNT(*) AS n FROM p").to_pandas()["n"][0] == 3

    # The product engine's geoparquet branch reads <dir>/partition/. The
    # boundary holds the whole 4 ha "big" feature and nothing else.
    boundary = Geometry(box(-50, -50, 250, 250), crs="EPSG:6933").to_crs(4326)
    area = _get_area_m2_from_dataset_geometry(
        sedona.db.connect(), str(out / "features.parquet"), "geoparquet", boundary, "x"
    )
    assert area == pytest.approx(40_000, abs=0.05)


def test_layer_column_lets_three_conversions_share_one_dataset(
    tmp_path: Path, steps_dir: Path
) -> None:
    out = tmp_path / "combined"
    common = [
        "--target-location",
        str(out),
        "--target-filename",
        "combined",
        "--partition",
    ]
    convert(
        "--source",
        str(VECTOR / "layers.gpkg"),
        "--layer",
        "parks",
        "--layer-column-value",
        "parks",
        *common,
    )
    convert(
        "--source",
        str(VECTOR / "layers.gpkg"),
        "--layer",
        "forests",
        "--layer-column-value",
        "forests",
        "--append",
        *common,
    )
    convert(
        "--source",
        str(VECTOR / "features_shp.zip"),
        "--where",
        "name = 'east'",
        "--layer-column-value",
        "reserves",
        "--append",
        *common,
    )
    gdf = gpd.read_parquet(out / "combined.parquet")
    assert sorted(gdf["layer"].value_counts().items()) == [
        ("forests", 1),
        ("parks", 2),
        ("reserves", 1),
    ]
    partitioned = gpd.read_parquet(out / "partition")
    assert len(partitioned) == 4

    # A retried conversion replaces its own layer rather than duplicating it.
    convert(
        "--source",
        str(VECTOR / "layers.gpkg"),
        "--layer",
        "parks",
        "--layer-column-value",
        "parks",
        "--append",
        *common,
    )
    assert len(gpd.read_parquet(out / "combined.parquet")) == 4
    assert len(gpd.read_parquet(out / "partition")) == 4
    step = last_step(steps_dir)
    assert step["inputs"]["layer_column_value"] == "parks"
    assert step["inputs"]["append"] is True
    assert step["outputs"]["replaced_rows"] == 2


def test_append_refusals(tmp_path: Path) -> None:
    with pytest.raises(CSDRException, match="needs --layer-column-value"):
        convert(
            "--source",
            str(VECTOR / "features.parquet"),
            "--target-location",
            str(tmp_path),
            "--append",
        )
    convert(
        "--source",
        str(VECTOR / "features.parquet"),
        "--target-location",
        str(tmp_path),
        "--layer-column-value",
        "a",
    )
    with pytest.raises(CSDRException, match="Reproject one of them"):
        convert(
            "--source",
            str(VECTOR / "features.geojson"),
            "--target-location",
            str(tmp_path),
            "--target-filename",
            "features",
            "--layer-column-value",
            "b",
            "--append",
        )
    with pytest.raises(CSDRException, match="already has a 'layer' column"):
        convert(
            "--source",
            str(tmp_path / "features.parquet"),
            "--target-location",
            str(tmp_path / "again"),
            "--layer-column-value",
            "c",
        )


def test_declared_source_hash_is_verified_before_writing(
    tmp_path: Path, steps_dir: Path
) -> None:
    source = VECTOR / "features.geojson"
    with pytest.raises(CSDRException, match="SHA-256 mismatch"):
        convert(
            "--source",
            str(source),
            "--target-location",
            str(tmp_path),
            "--source-sha256",
            "0" * 64,
        )
    assert not list(tmp_path.glob("*.parquet"))
    with pytest.raises(CSDRException, match="64 hexadecimal"):
        convert(
            "--source",
            str(source),
            "--target-location",
            str(tmp_path),
            "--source-sha256",
            "sha256:abc",
        )
    convert(
        "--source",
        str(source),
        "--target-location",
        str(tmp_path),
        "--source-sha256",
        sha256(source).upper(),
    )
    assert last_step(steps_dir)["inputs"]["source_sha256"] == sha256(source)


def test_invalid_geometries_are_repaired_and_recorded(
    tmp_path: Path, steps_dir: Path
) -> None:
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    source = tmp_path / "bowtie.geojson"
    gpd.GeoDataFrame({"name": ["b"]}, geometry=[bowtie], crs=4326).to_file(source)

    convert("--source", str(source), "--target-location", str(tmp_path / "fixed"))
    fixed = gpd.read_parquet(tmp_path / "fixed" / "bowtie.parquet")
    assert fixed.geometry.is_valid.all()
    assert fixed.geometry.iloc[0].area == pytest.approx(0.5)
    notes = last_step(steps_dir)["inputs"]["method_notes"]
    assert "1 invalid geometries repaired" in notes[0]

    convert(
        "--source",
        str(source),
        "--target-location",
        str(tmp_path / "raw"),
        "--no-make-valid",
    )
    assert not gpd.read_parquet(tmp_path / "raw" / "bowtie.parquet").is_valid.any()
    assert "kept unrepaired" in last_step(steps_dir)["inputs"]["method_notes"][0]


def test_geometry_sets_get_csdr_id_and_name(tmp_path: Path) -> None:
    convert(
        "--source",
        str(VECTOR / "features.geojson"),
        "--target-location",
        str(tmp_path),
        "--name-field",
        "name",
        "--geometry-id",
        "fixture-regions",
    )
    gdf = gpd.read_parquet(tmp_path / "features.parquet")
    assert list(gdf["csdr-name"]) == ["big", "east", "north"]
    assert gdf["csdr-id"].is_unique
    with pytest.raises(CSDRException, match="needs --name-field"):
        convert(
            "--source",
            str(VECTOR / "features.geojson"),
            "--target-location",
            str(tmp_path / "x"),
            "--geometry-id",
            "x",
        )


def test_no_overwrite_skips_without_reading(tmp_path: Path, steps_dir: Path) -> None:
    args = [
        "--source",
        str(VECTOR / "features.parquet"),
        "--target-location",
        str(tmp_path),
    ]
    convert(*args)
    before = (tmp_path / "features.parquet").stat().st_mtime_ns
    convert(*args, "--no-overwrite")
    assert (tmp_path / "features.parquet").stat().st_mtime_ns == before
    assert last_step(steps_dir)["outputs"]["skipped"]


def test_store_url_bare_shapefile_is_staged_with_its_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps_dir: Path
) -> None:
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    with zipfile.ZipFile(VECTOR / "features_shp.zip") as archive:
        for name in archive.namelist():
            (bucket / Path(name).name).write_bytes(archive.read(name))

    def store_for(url: str, **kwargs: object) -> LocalStore:
        if url.startswith("s3://"):
            return LocalStore(bucket)
        return LocalStore(url, mkdir=True)

    monkeypatch.setattr("csdr.vector_convert.get_store_with_prefix_from_url", store_for)
    out = tmp_path / "out"
    convert("--source", "s3://bucket/features.shp", "--target-location", str(out))
    assert len(gpd.read_parquet(out / "features.parquet")) == 3
    inputs = last_step(steps_dir)["inputs"]
    assert inputs["source_sha256"] == sha256(bucket / "features.shp")
    assert inputs["source_member_sha256"] == {
        name: sha256(bucket / name)
        for name in ("features.shx", "features.dbf", "features.prj", "features.cpg")
    }


def fake_tippecanoe(calls: list) -> object:
    def run(command: list[str], check: bool) -> None:
        geojson = json.loads(Path(command[-1]).read_text())
        calls.append((command, geojson))
        Path(command[command.index("--output") + 1]).write_bytes(b"PMTiles")

    return run


def test_pmtiles_use_an_epsg4326_copy_and_the_zip_to_parquet_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, steps_dir: Path
) -> None:
    calls: list = []
    monkeypatch.setattr("csdr.vector_convert.subprocess.run", fake_tippecanoe(calls))
    convert(
        "--source",
        str(VECTOR / "features.parquet"),
        "--target-location",
        str(tmp_path),
        "--layer-column-value",
        "parks",
        "--create-pmtiles",
    )
    assert (tmp_path / "features.pmtiles").read_bytes() == b"PMTiles"
    command, geojson = calls[0]
    assert command[:10] == [
        "tippecanoe",
        "--force",
        "-z",
        "10",
        "--no-simplification-of-shared-nodes",
        "--simplification",
        "10",
        "--drop-densest-as-needed",
        "--layer",
        "data",
    ]
    x, y = geojson["features"][0]["geometry"]["coordinates"][0][0]
    assert abs(x) < 0.01 and abs(y) < 0.01  # degrees, not EPSG:6933 metres
    assert set(geojson["features"][0]["properties"]) == {"layer"}
    # The native-CRS parquet is unchanged by the tile copy.
    assert gpd.read_parquet(tmp_path / "features.parquet").crs.to_epsg() == 6933
    assert (
        last_step(steps_dir)["outputs"]["pmtiles_url"] == f"{tmp_path}/features.pmtiles"
    )


@pytest.mark.skipif(
    shutil.which("tippecanoe") is None, reason="tippecanoe not installed"
)
def test_pmtiles_with_real_tippecanoe(tmp_path: Path) -> None:
    convert(
        "--source",
        str(VECTOR / "features.geojson"),
        "--target-location",
        str(tmp_path),
        "--name-field",
        "name",
        "--create-pmtiles",
    )
    assert (tmp_path / "features.pmtiles").read_bytes()[:7] == b"PMTiles"


def zip_member(tmp_path: Path, source: Path, member: str) -> Path:
    archive_path = tmp_path / f"{Path(member).name}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(source, member)
    return archive_path


@pytest.mark.parametrize(
    "member,kind",
    [("data/regions.geojson", "GeoJSON"), ("data/regions.gpkg", "GeoPackage")],
)
def test_zip_to_parquet_accepts_geojson_and_geopackage_members(
    tmp_path: Path, steps_dir: Path, member: str, kind: str
) -> None:
    source = tmp_path / Path(member).name
    gpd.read_file(VECTOR / "features.geojson").to_file(source)
    archive = zip_member(tmp_path, source, member)
    out = tmp_path / "out"
    result = runner.invoke(
        conversion_app,
        [
            "zip-to-parquet",
            "--source-zip-location",
            str(archive),
            "--source-internal-path-name",
            member,
            "--target-location",
            str(out),
            "--name-field",
            "name",
            "--no-create-pmtiles",
        ],
    )
    assert result.exit_code == 0, result.output
    gdf = gpd.read_parquet(out / "regions.parquet")
    assert list(gdf["csdr-name"]) == ["big", "east", "north"]
    step = last_step(steps_dir)
    assert step["label"] == f"Convert zipped {kind} to GeoParquet"
    assert step["inputs"]["source_internal_path_name"] == member
    assert step["inputs"]["source_sha256"] == sha256(archive)
    assert step["outputs"] == {"target_url": f"{out}/regions.parquet"}


def test_zip_to_parquet_shapefile_keeps_its_historical_output(
    tmp_path: Path, steps_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # zip-to-parquet neither repairs nor records invalid geometries, so it must
    # not pay for the per-feature validity scan.
    monkeypatch.setattr(
        "csdr.vector_convert.repair_geometries",
        lambda *args: pytest.fail("zip-to-parquet must not scan geometry validity"),
    )
    result = runner.invoke(
        conversion_app,
        [
            "zip-to-parquet",
            "--source-zip-location",
            str(VECTOR / "features_shp.zip"),
            "--source-internal-path-name",
            "features/features.shp",
            "--target-location",
            str(tmp_path),
            "--name-field",
            "name",
            "--no-create-pmtiles",
        ],
    )
    assert result.exit_code == 0, result.output
    gdf = gpd.read_parquet(tmp_path / "features.parquet")
    assert gdf.crs.to_epsg() == 6933
    assert {"csdr-id", "csdr-name", "name", "state", "kind"} <= set(gdf.columns)
    assert last_step(steps_dir)["label"] == "Convert zipped shapefile to GeoParquet"


def write_provenance(path: Path, **fields: str) -> str:
    path.write_text(json.dumps(fields))
    return str(path)


def test_process_geometry_measures_the_area_of_a_converted_dataset(
    tmp_path: Path,
) -> None:
    # The boundary holds the whole 4 ha "big" feature and no other feature
    # (equal-area construction in EPSG:6933).
    dataset_dir = tmp_path / "datasets" / "features"
    convert(
        "--source",
        str(VECTOR / "features_shp.zip"),
        "--target-location",
        str(dataset_dir),
        "--target-filename",
        "features",
        "--partition",
    )
    boundaries = gpd.GeoDataFrame(
        {"csdr-id": ["around"], "csdr-name": ["Around"]},
        geometry=[box(-50, -50, 250, 250)],
        crs="EPSG:6933",
    )
    boundaries.to_parquet(tmp_path / "boundaries.parquet")
    geometry_provenance = write_provenance(
        tmp_path / "boundaries.parquet.provenance.json",
        dataUrl=str(tmp_path / "boundaries.parquet"),
    )
    dataset_provenance = write_provenance(
        dataset_dir / "features.parquet.provenance.json",
        dataUrl=str(dataset_dir / "features.parquet"),
        dataType="geoparquet",
    )
    result = runner.invoke(
        products_app,
        [
            "process-geometry",
            "--product-id",
            "p",
            "--run-id",
            "r",
            "--geometry-provenance-url",
            geometry_provenance,
            "--dataset-provenance-url",
            dataset_provenance,
            "--indicators-to-extract",
            json.dumps({"sum-feature-area": {"indicator-name": "feature"}}),
            "--datetime",
            "2024",
            "--target-location",
            str(tmp_path / "product"),
            "--geometry-id",
            "around",
        ],
    )
    assert result.exit_code == 0, result.output
    output = json.loads((tmp_path / "product/feature/2024/p-around.json").read_text())
    assert output["indicators"]["sum-feature-area"] == pytest.approx(40_000, abs=0.05)


@pytest.fixture
def filter_file(tmp_path: Path) -> Path:
    # A state-boundary-like filter, stored in EPSG:4326 so it is reprojected to
    # the EPSG:6933 source. It crosses "big" and only touches "east" (x = 1100).
    path = tmp_path / "filter.geojson"
    gpd.GeoDataFrame(
        geometry=[box(150, 0, 250, 200), box(1100, 0, 1200, 100)], crs="EPSG:6933"
    ).to_crs(4326).to_file(path)
    return path


def test_filter_geometry_keeps_intersecting_rows_and_records_the_filter(
    tmp_path: Path, steps_dir: Path, filter_file: Path
) -> None:
    out = tmp_path / "out"
    convert(
        "--source",
        str(VECTOR / "features.parquet"),
        "--target-location",
        str(out),
        "--filter-geometry",
        str(filter_file),
    )
    gdf = gpd.read_parquet(out / "features.parquet")
    assert sorted(gdf["name"]) == ["big", "east"]  # "north" is dropped
    assert gdf.loc[gdf["name"] == "big"].area.iloc[0] == pytest.approx(40_000)
    inputs = last_step(steps_dir)["inputs"]
    assert inputs["filter_geometry"] == str(filter_file)
    assert inputs["filter_geometry_sha256"] == sha256(filter_file)
    assert inputs["clip"] is False
    assert (inputs["rows_before_filter"], inputs["rows_after_filter"]) == (3, 2)


def test_clip_cuts_crossing_features_to_the_filter(
    tmp_path: Path, steps_dir: Path
) -> None:
    # Same shapes as filter_file, kept in the source CRS so the touch is exact.
    filter_file = tmp_path / "filter.parquet"
    gpd.GeoDataFrame(
        geometry=[box(150, 0, 250, 200), box(1100, 0, 1200, 100)], crs="EPSG:6933"
    ).to_parquet(filter_file)
    out = tmp_path / "out"
    convert(
        "--source",
        str(VECTOR / "features.parquet"),
        "--target-location",
        str(out),
        "--filter-geometry",
        str(filter_file),
        "--clip",
        "--partition",
    )
    gdf = gpd.read_parquet(out / "features.parquet")
    # "east" only touches the filter: its clipped part is a line, dropped as a
    # non-polygonal sliver. "big" keeps its inside part, 50 m x 200 m.
    assert list(gdf["name"]) == ["big"]
    assert list(gdf.geom_type) == ["Polygon"]
    assert gdf.area.iloc[0] == pytest.approx(10_000, abs=1)
    step = last_step(steps_dir)
    assert step["inputs"]["clip"] is True
    assert step["inputs"]["rows_after_filter"] == 1
    assert any(
        "clipped to the --filter-geometry" in n for n in step["inputs"]["method_notes"]
    )
    assert len(gpd.read_parquet(out / "partition")) == 1


def test_clip_without_filter_geometry_is_refused(tmp_path: Path) -> None:
    with pytest.raises(
        CSDRException, match="--clip is only valid with --filter-geometry"
    ):
        convert(
            "--source",
            str(VECTOR / "features.parquet"),
            "--target-location",
            str(tmp_path),
            "--clip",
        )
    assert not list(tmp_path.glob("*.parquet"))


def test_filter_with_no_intersecting_rows_is_refused(tmp_path: Path) -> None:
    far = tmp_path / "far.geojson"
    gpd.GeoDataFrame(geometry=[box(5000, 5000, 6000, 6000)], crs="EPSG:6933").to_file(
        far
    )
    with pytest.raises(CSDRException, match="No source features intersect"):
        convert(
            "--source",
            str(VECTOR / "features.parquet"),
            "--target-location",
            str(tmp_path),
            "--filter-geometry",
            str(far),
        )


def test_repair_keeps_polygonal_parts_only_for_polygon_features() -> None:
    from shapely.geometry import GeometryCollection, LineString, Point

    from csdr.vector_convert import make_valid_polygonal

    collection = GeometryCollection([box(0, 0, 1, 1), LineString([(1, 1), (2, 2)])])
    geometries = gpd.GeoSeries([collection, LineString([(0, 0), (1, 1)]), Point(0, 0)])
    fixed = make_valid_polygonal(geometries, gpd.pd.Series([True, False, False]))
    assert fixed.iloc[0].geom_type == "Polygon"
    assert fixed.iloc[0].area == pytest.approx(1)
    assert fixed.iloc[1].geom_type == "LineString"  # lines and points untouched
    assert fixed.iloc[2].geom_type == "Point"


def test_existing_partitions_are_rewritten_on_append_and_overwrite(
    tmp_path: Path, steps_dir: Path
) -> None:
    out = tmp_path / "combined"
    common = ["--target-location", str(out), "--target-filename", "combined"]
    convert(
        "--source",
        str(VECTOR / "layers.gpkg"),
        "--layer",
        "parks",
        "--layer-column-value",
        "parks",
        "--partition",
        *common,
    )
    assert "partition_rewritten" not in last_step(steps_dir)["outputs"]

    # --append without --partition: the stale partitions must not survive.
    convert(
        "--source",
        str(VECTOR / "layers.gpkg"),
        "--layer",
        "forests",
        "--layer-column-value",
        "forests",
        "--append",
        *common,
    )
    partitioned = gpd.read_parquet(out / "partition")
    assert sorted(partitioned["layer"]) == ["forests", "parks", "parks"]
    outputs = last_step(steps_dir)["outputs"]
    assert outputs["partition_rewritten"] is True
    assert outputs["partition_count"] >= 1
    # The grid used for the rewrite is recorded even without --partition.
    assert last_step(steps_dir)["inputs"]["grid_chunks"] == 10

    # A plain overwrite without --partition also rewrites them.
    convert(
        "--source",
        str(VECTOR / "features.geojson"),
        "--where",
        "name = 'east'",
        *common,
    )
    assert list(gpd.read_parquet(out / "partition")["name"]) == ["east"]
    assert last_step(steps_dir)["outputs"]["partition_rewritten"] is True


def test_one_dataset_per_target_folder(tmp_path: Path) -> None:
    convert(
        "--source",
        str(VECTOR / "features.parquet"),
        "--target-location",
        str(tmp_path),
        "--target-filename",
        "one",
        "--partition",
    )
    with pytest.raises(
        CSDRException, match="One dataset per target folder; found partitions for one"
    ):
        convert(
            "--source",
            str(VECTOR / "features.parquet"),
            "--target-location",
            str(tmp_path),
            "--target-filename",
            "two",
            "--partition",
        )
    assert not (tmp_path / "two.parquet").exists()
    assert sorted(p.name.split("_")[0] for p in (tmp_path / "partition").iterdir()) == [
        "one"
    ]
    # Writing another slug without partitions leaves the folder's partitions alone.
    convert(
        "--source",
        str(VECTOR / "features.parquet"),
        "--target-location",
        str(tmp_path),
        "--target-filename",
        "plain",
    )
    assert (tmp_path / "plain.parquet").exists()


def test_null_and_empty_geometries_are_dropped_and_partition_names_stay_integer(
    tmp_path: Path, steps_dir: Path
) -> None:
    collapsed = Polygon([(0, 0), (1, 1), (2, 2), (0, 0)])  # make_valid -> empty
    source = tmp_path / "src.parquet"
    gpd.GeoDataFrame(
        {"name": ["ok", "null", "collapsed"]},
        geometry=[box(150, -34, 151, -33), None, collapsed],
        crs=4326,
    ).to_parquet(source)
    out = tmp_path / "out"
    convert("--source", str(source), "--target-location", str(out), "--partition")
    assert list(gpd.read_parquet(out / "src.parquet")["name"]) == ["ok"]
    assert last_step(steps_dir)["outputs"]["dropped_empty_geometries"] == 2
    assert [p.name for p in (out / "partition").iterdir()] == ["src_18_3.parquet"]


def test_partition_file_names_are_integer_bins_even_with_missing_centroids(
    tmp_path: Path,
) -> None:
    from csdr.utils import partition_parquet

    gdf = gpd.GeoDataFrame(
        {"name": ["ok", "null"]}, geometry=[box(150, -34, 151, -33), None], crs=4326
    )
    written = partition_parquet(LocalStore(tmp_path), gdf, file_prefix="x")
    assert written == 1
    assert [p.name for p in (tmp_path / "partition").iterdir()] == ["x_18_3.parquet"]
    assert len(gdf) == 2  # the caller's frame is not modified


def test_local_bare_shapefile_records_its_sidecar_hashes(
    tmp_path: Path, steps_dir: Path
) -> None:
    folder = tmp_path / "bare"
    folder.mkdir()
    with zipfile.ZipFile(VECTOR / "features_shp.zip") as archive:
        for name in archive.namelist():
            (folder / Path(name).name).write_bytes(archive.read(name))
    out = tmp_path / "out"
    convert("--source", str(folder / "features.shp"), "--target-location", str(out))
    assert len(gpd.read_parquet(out / "features.parquet")) == 3
    inputs = last_step(steps_dir)["inputs"]
    assert inputs["source_sha256"] == sha256(folder / "features.shp")
    assert inputs["source_member_sha256"] == {
        name: sha256(folder / name)
        for name in ("features.shx", "features.dbf", "features.prj", "features.cpg")
    }
