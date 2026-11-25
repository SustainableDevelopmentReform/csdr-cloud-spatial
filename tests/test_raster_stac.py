"""Generic raster conversion to COGs and STAC items (csdr convert raster-to-stac).

Fixtures are generated at test time: a 50 x 50 uint16 categorical raster with
nodata and a 50 x 50 float32 continuous raster with NaN, both in EPSG:3577 at
30 m. STAC items are validated offline against the extension schemas committed
under tests/data/raster/schemas/ (copied from stac-extensions.github.io).
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pystac
import pytest
import rasterio
import rasterio.warp
from obstore.store import LocalStore
from pystac.validation.stac_validator import JsonSchemaSTACValidator
from rasterio.transform import from_origin
from typer.testing import CliRunner

from csdr.cli_conversion import conversion_app
from csdr.raster_stac import (
    classification_classes,
    plan_tiles,
    read_table,
    tile_size_from_degrees,
)
from csdr.utils import CSDRException

SCHEMAS = Path(__file__).parent / "data" / "raster" / "schemas"
runner = CliRunner()

ORIGIN_X, ORIGIN_Y, RES = 1_000_000.0, -3_000_000.0, 30.0
NODATA = 65535
CRS = "EPSG:3577"


@pytest.fixture(autouse=True)
def steps_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "steps"
    monkeypatch.setenv("CSDR_PROVENANCE_STEPS_DIR", str(directory))
    monkeypatch.setenv(
        "IMAGE_REPO", "https://github.com/example/toolkit/tree/fixture-commit/"
    )
    return directory


def last_step(steps_dir: Path) -> dict:
    return json.loads(max(steps_dir.glob("step-*.json")).read_text())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def categorical_values() -> np.ndarray:
    """Classes 0-3 in 10-pixel bands, nodata in tile (0, 2) and column 0."""
    rows, cols = np.indices((50, 50))
    values = ((rows // 10 + cols // 10) % 4).astype("uint16")
    values[0:20, 40:50] = NODATA  # the whole top-right tile at --tile-size 20
    values[20:50, 0] = NODATA
    return values


def write_raster(
    path: Path, values: np.ndarray, nodata: float | None, crs: str = CRS
) -> Path:
    profile = {
        "driver": "GTiff",
        "width": values.shape[1],
        "height": values.shape[0],
        "count": 1,
        "dtype": values.dtype.name,
        "crs": crs,
        "transform": from_origin(ORIGIN_X, ORIGIN_Y, RES, RES),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as output:
        output.write(values, 1)
    return path


@pytest.fixture
def categorical(tmp_path: Path) -> Path:
    return write_raster(tmp_path / "classes.tif", categorical_values(), NODATA)


@pytest.fixture
def continuous(tmp_path: Path) -> Path:
    values = np.linspace(0, 1, 2500, dtype="float32").reshape(50, 50)
    values[0:20, 0:20] = np.nan  # the whole top-left tile at --tile-size 20
    values[45:50, :] = np.nan
    return write_raster(tmp_path / "index.tif", values, float("nan"))


CLASS_ROWS = [
    {"Value": 0, "ClassName": "Not classified", "Group": None},
    {"Value": 1, "ClassName": "Open forest (dry)", "Group": "forest"},
    {"Value": 2, "ClassName": "Grassland", "Group": "grass"},
    {"Value": 3, "ClassName": "Wetland / marsh", "Group": "wet"},
]


@pytest.fixture
def class_dbf(tmp_path: Path) -> Path:
    import pyogrio

    path = tmp_path / "classes.tif.vat.dbf"
    pyogrio.write_dataframe(pd.DataFrame(CLASS_ROWS), path, driver="ESRI Shapefile")
    return path


def convert(*args: str) -> None:
    result = runner.invoke(conversion_app, ["raster-to-stac", *args])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output


def items(target: Path) -> dict[str, dict]:
    return {
        path.stem: json.loads(path.read_text())
        for path in sorted((target / "items").glob("*.json"))
    }


class OfflineValidator(JsonSchemaSTACValidator):
    """pystac's validator with the core and extension schemas, never the network."""

    def __init__(self) -> None:
        super().__init__()
        for name, uri in [
            ("classification_v2.0.0", "classification/v2.0.0"),
            ("projection_v1.1.0", "projection/v1.1.0"),
            ("projection_v2.0.0", "projection/v2.0.0"),
            ("raster_v1.1.0", "raster/v1.1.0"),
        ]:
            schema = json.loads((SCHEMAS / f"{name}.json").read_text())
            self.schema_cache[
                f"https://stac-extensions.github.io/{uri}/schema.json"
            ] = schema

    def _get_schema(self, schema_uri: str) -> dict:
        if schema_uri not in self.schema_cache:
            raise AssertionError(f"validation would fetch {schema_uri}")
        return self.schema_cache[schema_uri]


def validate(document: dict) -> None:
    validator = OfflineValidator()
    pystac.validation.validate_dict(document, validator=validator)
    # Reading migrates proj v1.1 to v2.0; the migrated object must validate too.
    stac_object = (
        pystac.Item.from_dict(document)
        if document["type"] == "Feature"
        else pystac.Collection.from_dict(document)
    )
    stac_object.validate(validator=validator)


# ---------------------------------------------------------------------------
# Tiling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(50, 50), (20, 20), (7, 13), (1, 50), (64, 64)])
def test_planned_tiles_cover_every_pixel_exactly_once(size: tuple[int, int]) -> None:
    counts = np.zeros((50, 50), int)
    for tile in plan_tiles(50, 50, *size):
        window = tile.window
        counts[
            window.row_off : window.row_off + window.height,
            window.col_off : window.col_off + window.width,
        ] += 1
    assert (counts == 1).all()


def test_tiles_cover_the_source_once_and_keep_values_dtype_and_nodata(
    tmp_path: Path, categorical: Path, steps_dir: Path
) -> None:
    out = tmp_path / "out"
    convert(
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023-01-01", "--tile-size", "20",
    )  # fmt: skip
    source = categorical_values()
    cogs = sorted((out / "cogs").glob("*.tif"))
    assert [c.stem for c in cogs] == [
        f"classes_{r}_{c}" for r in range(3) for c in range(3) if (r, c) != (0, 2)
    ]
    counts = np.zeros(source.shape, int)
    for cog in cogs:
        with rasterio.open(cog) as dataset:
            assert dataset.crs.to_epsg() == 3577
            assert dataset.res == (RES, RES)
            assert dataset.dtypes[0] == "uint16"
            assert dataset.nodata == NODATA
            assert dataset.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
            assert dataset.profile["compress"] == "deflate"
            col = round((dataset.transform.c - ORIGIN_X) / RES)
            row = round((ORIGIN_Y - dataset.transform.f) / RES)
            window = (slice(row, row + dataset.height), slice(col, col + dataset.width))
            counts[window] += 1
            np.testing.assert_array_equal(dataset.read(1), source[window])
    # Every pixel is in exactly one COG except the skipped all-nodata tile.
    assert (counts[0:20, 40:50] == 0).all()
    counts[0:20, 40:50] = 1
    assert (counts == 1).all()

    step = last_step(steps_dir)
    assert step["outputs"]["cogs_count"] == 8
    assert step["outputs"]["items_count"] == 8
    assert step["outputs"]["tiles_planned"] == 9
    assert step["outputs"]["skipped_all_nodata_tiles"] == 1
    assert step["inputs"]["source_sha256"] == sha256(categorical)
    assert step["inputs"]["tile_size_pixels"] == [20, 20]
    assert step["inputs"]["nodata"] == NODATA
    assert step["inputs"]["source_crs"] == "EPSG:3577"


def test_one_item_by_default(tmp_path: Path, categorical: Path) -> None:
    out = tmp_path / "out"
    convert(
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023-01-01",
    )  # fmt: skip
    assert list(items(out)) == ["classes_0_0"]
    with rasterio.open(out / "cogs" / "classes_0_0.tif") as dataset:
        np.testing.assert_array_equal(dataset.read(1), categorical_values())


def test_tile_degrees_resolves_whole_pixels_and_is_recorded(
    tmp_path: Path, categorical: Path, steps_dir: Path
) -> None:
    out = tmp_path / "out"
    # 0.01 degrees is about 1 km here: roughly 33-37 pixels of 30 m.
    convert(
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023-01-01",
        "--tile-degrees", "0.01",
    )  # fmt: skip
    step = last_step(steps_dir)
    width, height = step["inputs"]["tile_size_pixels"]
    assert 30 <= width <= 40 and 30 <= height <= 40
    assert step["inputs"]["tile_degrees"] == 0.01
    assert any(
        "--tile-degrees 0.01 resolved" in n for n in step["inputs"]["method_notes"]
    )


def test_tile_degrees_is_exact_for_a_geographic_crs() -> None:
    transform = from_origin(150, -30, 0.001, 0.0005)
    assert tile_size_from_degrees(
        rasterio.crs.CRS.from_epsg(4326), transform, 5000, 5000, 1
    ) == (1000, 2000)


def test_tile_options_are_exclusive(tmp_path: Path, categorical: Path) -> None:
    with pytest.raises(CSDRException, match="not both"):
        convert(
            "--source", str(categorical), "--target-location", str(tmp_path / "o"),
            "--asset-name", "a", "--datetime", "2023", "--tile-size", "10",
            "--tile-degrees", "1",
        )  # fmt: skip


# ---------------------------------------------------------------------------
# STAC items and collection
# ---------------------------------------------------------------------------


def test_items_and_collection_validate_with_classification(
    tmp_path: Path, categorical: Path, class_dbf: Path, steps_dir: Path
) -> None:
    out = tmp_path / "out"
    convert(
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023-01-01",
        "--tile-size", "20", "--collection", "land-cover",
        "--classification-table", str(class_dbf), "--class-extra-columns", "Group",
    )  # fmt: skip
    documents = items(out)
    assert len(documents) == 8
    for item_id, document in documents.items():
        validate(document)
        assert document["id"] == item_id
        assert document["collection"] == "land-cover"
        assert document["properties"]["datetime"] == "2023-01-01T00:00:00Z"
        assert document["properties"]["proj:epsg"] == 3577
        asset = document["assets"]["landcover"]
        assert asset["href"] == str(out / "cogs" / f"{item_id}.tif")
        assert asset["type"] == pystac.MediaType.COG
        assert asset["roles"] == ["data"]
        band = asset["raster:bands"][0]
        assert band["data_type"] == "uint16"
        assert band["nodata"] == NODATA
        assert asset["classification:classes"][1] == {
            "value": 1,
            "name": "Open-forest-dry",
            "title": "Open forest (dry)",
            "Group": "forest",
        }
        links = {link["rel"]: link["href"] for link in document["links"]}
        assert links == {
            "collection": str(out / "collection.json"),
            "self": str(out / "items" / f"{item_id}.json"),
        }
    collection = json.loads((out / "collection.json").read_text())
    validate(collection)
    assert collection["id"] == "land-cover"
    bbox = collection["extent"]["spatial"]["bbox"][0]
    for document in documents.values():
        b = document["bbox"]
        assert (
            bbox[0] <= b[0] and bbox[1] <= b[1] and b[2] <= bbox[2] and b[3] <= bbox[3]
        )
    classes = collection["item_assets"]["landcover"]["classification:classes"]
    assert [c["value"] for c in classes] == [0, 1, 2, 3]
    step = last_step(steps_dir)
    assert step["inputs"]["classification_table_sha256"] == sha256(class_dbf)
    assert step["outputs"]["class_count"] == 4


def test_item_properties_are_merged_and_datetime_is_reserved(
    tmp_path: Path, categorical: Path
) -> None:
    out = tmp_path / "out"
    convert(
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2020-07-01T00:00:00+10:00",
        "--item-properties", '{"epoch": "2020-21"}',
    )  # fmt: skip
    (document,) = items(out).values()
    assert document["properties"]["epoch"] == "2020-21"
    assert document["properties"]["datetime"] == "2020-06-30T14:00:00Z"
    with pytest.raises(CSDRException, match="--datetime"):
        convert(
            "--source", str(categorical), "--target-location", str(out),
            "--asset-name", "landcover", "--datetime", "2020",
            "--item-properties", '{"datetime": "2021-01-01"}',
        )  # fmt: skip


def test_a_second_epoch_extends_the_shared_collection(
    tmp_path: Path, categorical: Path
) -> None:
    out = tmp_path / "out"
    for slug, when in [("epoch-a", "2010-07-01"), ("epoch-b", "2020-07-01")]:
        convert(
            "--source", str(categorical), "--target-location", str(out),
            "--asset-name", "landcover", "--datetime", when,
            "--collection", "land-cover", "--target-filename", slug,
        )  # fmt: skip
    assert sorted(items(out)) == ["epoch-a_0_0", "epoch-b_0_0"]
    collection = json.loads((out / "collection.json").read_text())
    (interval,) = collection["extent"]["temporal"]["interval"]
    assert interval[0].startswith("2010-07-01") and interval[1].startswith("2020-07-01")
    with pytest.raises(CSDRException, match="already holds collection"):
        convert(
            "--source", str(categorical), "--target-location", str(out),
            "--asset-name", "landcover", "--datetime", "2023",
            "--collection", "other",
        )  # fmt: skip


def test_retiling_removes_the_earlier_tiling_of_the_same_slug_only(
    tmp_path: Path, categorical: Path, steps_dir: Path
) -> None:
    out = tmp_path / "out"
    common = ["--target-location", str(out), "--asset-name", "landcover"]
    convert("--source", str(categorical), *common, "--datetime", "2023",
            "--tile-size", "20")  # fmt: skip
    # Another epoch (slug) in the same target and collection must survive.
    convert("--source", str(categorical), *common, "--datetime", "2010",
            "--target-filename", "other", "--collection", "classes")  # fmt: skip
    convert("--source", str(categorical), *common, "--datetime", "2023",
            "--tile-size", "25")  # fmt: skip
    expected = ["classes_0_0", "classes_0_1", "classes_1_0", "classes_1_1", "other_0_0"]
    assert sorted(items(out)) == expected
    assert sorted(p.stem for p in (out / "cogs").glob("*.tif")) == expected
    outputs = last_step(steps_dir)["outputs"]
    assert outputs["stale_items_removed"] == 4  # 1_2, 2_0, 2_1, 2_2 of the 20 px tiling
    assert outputs["items_count"] == 4
    with rasterio.open(out / "cogs" / "classes_1_1.tif") as dataset:
        assert (dataset.width, dataset.height) == (25, 25)


def test_no_overwrite_keeps_earlier_items_with_a_warning_and_refuses_another_tiling(
    tmp_path: Path, categorical: Path, steps_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    out = tmp_path / "out"
    args = [
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023", "--tile-size", "20",
    ]  # fmt: skip
    convert(*args)
    # An item this tiling does not produce (as if left by an earlier run).
    (out / "items" / "classes_9_9.json").write_text(
        (out / "items" / "classes_2_2.json").read_text()
    )
    with caplog.at_level("WARNING", logger="csdr.raster_stac"):
        convert(*args, "--no-overwrite")
    outputs = last_step(steps_dir)["outputs"]
    assert outputs["stale_items_kept"] == 1 and outputs["stale_items_removed"] == 0
    assert "1 items of an earlier tiling remain" in caplog.text
    assert (out / "items" / "classes_9_9.json").exists()
    convert(*args)
    assert last_step(steps_dir)["outputs"]["stale_items_removed"] == 1
    assert not (out / "items" / "classes_9_9.json").exists()
    with pytest.raises(CSDRException, match="belongs to another tiling"):
        convert(*args[:-1], "25", "--no-overwrite")


def test_no_overwrite_resumes_without_rewriting(
    tmp_path: Path, categorical: Path, steps_dir: Path
) -> None:
    out = tmp_path / "out"
    args = [
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023", "--tile-size", "20",
    ]  # fmt: skip
    convert(*args)
    first = {p.name: p.stat().st_mtime_ns for p in (out / "cogs").glob("*.tif")}
    convert(*args, "--no-overwrite")
    assert {p.name: p.stat().st_mtime_ns for p in (out / "cogs").glob("*.tif")} == first
    outputs = last_step(steps_dir)["outputs"]
    assert outputs["reused_items"] == 8
    assert outputs["items_count"] == 8


# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------


def test_classification_tables_read_the_same_from_dbf_csv_json_and_rat(
    tmp_path: Path, class_dbf: Path
) -> None:
    csv = tmp_path / "classes.csv"
    pd.DataFrame(CLASS_ROWS).to_csv(csv, index=False)
    as_json = tmp_path / "classes.json"
    as_json.write_text(
        json.dumps(
            [
                {"value": r["Value"], "name": r["ClassName"], "Group": r["Group"]}
                for r in CLASS_ROWS
            ]
        )
    )
    rat = tmp_path / "classes.tif.aux.xml"
    rows = "".join(
        f'<Row index="{i}"><F>{r["Value"]}</F><F>{r["ClassName"]}</F>'
        f"<F>{r['Group'] or ''}</F></Row>"
        for i, r in enumerate(CLASS_ROWS)
    )
    rat.write_text(
        '<PAMDataset><PAMRasterBand band="1"><GDALRasterAttributeTable>'
        '<FieldDefn index="0"><Name>Value</Name><Type>0</Type><Usage>5</Usage></FieldDefn>'
        '<FieldDefn index="1"><Name>ClassName</Name><Type>2</Type><Usage>2</Usage></FieldDefn>'
        '<FieldDefn index="2"><Name>Group</Name><Type>2</Type><Usage>0</Usage></FieldDefn>'
        f"{rows}</GDALRasterAttributeTable></PAMRasterBand></PAMDataset>"
    )
    expected = classification_classes(
        read_table(class_dbf), extra_columns=["Group"], nodata=NODATA
    )
    assert expected[0] == {
        "value": 0,
        "name": "Not-classified",
        "title": "Not classified",
    }
    assert expected[3]["name"] == "Wetland-marsh"
    for table in (csv, as_json, rat):
        assert (
            classification_classes(read_table(table), extra_columns=["Group"])
            == expected
        )


def test_linear_binned_rat_takes_values_from_row0min_and_binsize(
    tmp_path: Path,
) -> None:
    rat = tmp_path / "binned.tif.aux.xml"
    rat.write_text(
        '<PAMDataset><PAMRasterBand band="1">'
        '<GDALRasterAttributeTable Row0Min="10" BinSize="5">'
        '<FieldDefn index="0"><Name>Class_Name</Name><Type>2</Type><Usage>2</Usage></FieldDefn>'
        '<Row index="0"><F>Low</F></Row><Row index="1"><F>High</F></Row>'
        "</GDALRasterAttributeTable></PAMRasterBand></PAMDataset>"
    )
    # The default --class-value-column (Value) is absent: the binned values apply.
    assert classification_classes(read_table(rat)) == [
        {"value": 10, "name": "Low", "title": "Low"},
        {"value": 15, "name": "High", "title": "High"},
    ]


def test_raster_values_without_a_class_are_recorded_and_warned(
    tmp_path: Path, categorical: Path, steps_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    partial = tmp_path / "partial.csv"
    pd.DataFrame(CLASS_ROWS[:3]).to_csv(partial, index=False)  # no class 3
    out = tmp_path / "out"
    with caplog.at_level("WARNING", logger="csdr.raster_stac"):
        convert(
            "--source", str(categorical), "--target-location", str(out),
            "--asset-name", "landcover", "--datetime", "2023", "--tile-size", "20",
            "--classification-table", str(partial),
        )  # fmt: skip
    assert last_step(steps_dir)["outputs"]["raster_values_without_class"] == {
        "count": 1,
        "values": [3],
        "tiles_checked": 8,
    }
    assert "1 raster values have no class" in caplog.text

    full = tmp_path / "full.csv"
    pd.DataFrame(CLASS_ROWS).to_csv(full, index=False)
    convert(
        "--source", str(categorical), "--target-location", str(tmp_path / "o2"),
        "--asset-name", "landcover", "--datetime", "2023",
        "--classification-table", str(full),
    )  # fmt: skip
    missing = last_step(steps_dir)["outputs"]["raster_values_without_class"]
    assert missing == {"count": 0, "values": [], "tiles_checked": 1}


def test_float_rasters_skip_the_class_coverage_check(
    tmp_path: Path, continuous: Path, steps_dir: Path
) -> None:
    table = tmp_path / "t.json"
    table.write_text(json.dumps([{"value": 1, "name": "one"}]))
    convert(
        "--source", str(continuous), "--target-location", str(tmp_path / "o"),
        "--asset-name", "index", "--datetime", "2023",
        "--classification-table", str(table),
    )  # fmt: skip
    assert "raster_values_without_class" not in last_step(steps_dir)["outputs"]


def test_classification_marks_the_nodata_class_and_refuses_bad_tables() -> None:
    table = pd.DataFrame({"value": [0, 255], "label": ["background", "no data"]})
    classes = classification_classes(table, nodata=255)
    assert classes[1] == {
        "value": 255,
        "name": "no-data",
        "title": "no data",
        "nodata": True,
    }
    colored = pd.DataFrame(
        {"value": [1, 2], "name": ["a", "b"], "color_hint": ["#00ff00", None]}
    )
    assert [c.get("color_hint") for c in classification_classes(colored)] == [
        "00FF00",
        None,
    ]
    with pytest.raises(CSDRException, match="six hex digits"):
        classification_classes(
            pd.DataFrame({"value": [1], "name": ["a"], "color_hint": ["green"]})
        )
    with pytest.raises(CSDRException, match="more than once"):
        classification_classes(pd.DataFrame({"value": [1, 1], "name": ["a", "b"]}))
    with pytest.raises(CSDRException, match="not an integer"):
        classification_classes(pd.DataFrame({"value": [1.5], "name": ["a"]}))
    with pytest.raises(CSDRException, match="collides"):
        classification_classes(
            pd.DataFrame({"value": [1], "name": ["a"], "title": ["b"]}),
            extra_columns=["title"],
        )
    with pytest.raises(CSDRException, match="No class name column"):
        classification_classes(pd.DataFrame({"value": [1], "code": ["a"]}))
    with pytest.raises(CSDRException, match="--class-value-column"):
        classification_classes(pd.DataFrame({"code": [1], "name": ["a"]}))


# ---------------------------------------------------------------------------
# Nodata, display copy, identity
# ---------------------------------------------------------------------------


def test_continuous_nan_nodata_is_preserved_and_all_nan_tiles_skipped(
    tmp_path: Path, continuous: Path, steps_dir: Path
) -> None:
    out = tmp_path / "out"
    convert(
        "--source", str(continuous), "--target-location", str(out),
        "--asset-name", "index", "--datetime", "2023-01-01", "--tile-size", "20",
    )  # fmt: skip
    documents = items(out)
    assert "index_0_0" not in documents and len(documents) == 8
    for document in documents.values():
        validate(document)
        band = document["assets"]["index"]["raster:bands"][0]
        assert band["data_type"] == "float32"
        assert band["nodata"] == "nan"
    with rasterio.open(out / "cogs" / "index_2_2.tif") as dataset:
        assert dataset.dtypes[0] == "float32"
        assert np.isnan(dataset.nodata)
        data = dataset.read(1)
    assert np.isnan(data[5:, :]).all() and np.isfinite(data[:5, :]).all()
    assert last_step(steps_dir)["inputs"]["nodata"] == "nan"


def test_nodata_option_sets_a_value_the_source_lacks(
    tmp_path: Path, steps_dir: Path
) -> None:
    values = categorical_values()
    values[values == NODATA] = 9
    source = write_raster(tmp_path / "bare.tif", values, None)
    out = tmp_path / "out"
    convert(
        "--source", str(source), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023", "--nodata", "9",
        "--tile-size", "20",
    )  # fmt: skip
    with rasterio.open(out / "cogs" / "bare_1_1.tif") as dataset:
        assert dataset.nodata == 9
    assert last_step(steps_dir)["outputs"]["skipped_all_nodata_tiles"] == 1
    notes = last_step(steps_dir)["inputs"]["method_notes"]
    assert any("declares no nodata; --nodata 9 set" in n for n in notes)
    with pytest.raises(CSDRException, match="outside the uint16 range"):
        convert(
            "--source", str(source), "--target-location", str(out),
            "--asset-name", "landcover", "--datetime", "2023", "--nodata", "-1",
        )  # fmt: skip


def test_display_copy_is_smaller_and_georeferenced(
    tmp_path: Path, categorical: Path, steps_dir: Path
) -> None:
    out, display = tmp_path / "out", tmp_path / "display"
    convert(
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023",
        "--display-copy", str(display),
    )  # fmt: skip
    path = display / "classes_display.tif"
    with rasterio.open(categorical) as source, rasterio.open(path) as copy:
        # Default: the coarser of 250 m and 10 x 30 m = 300 m, so 5 x 5 pixels.
        assert copy.res == (300, 300)
        assert (copy.width, copy.height) == (5, 5)
        assert copy.crs == source.crs
        assert copy.bounds == pytest.approx(source.bounds)
        assert copy.dtypes[0] == "uint16" and copy.nodata == NODATA
        assert copy.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
    assert path.stat().st_size < (out / "cogs" / "classes_0_0.tif").stat().st_size
    outputs = last_step(steps_dir)["outputs"]
    assert outputs["display_copy_url"] == str(path)
    assert outputs["display_resolution"] == [300, 300]
    assert outputs["display_resolution_requested"] == 300
    assert outputs["display_crs"] == "EPSG:3577"
    # 1500 m at 400 m is 3.75 pixels: 4 whole pixels over the exact bounds = 375 m.
    convert(
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023",
        "--display-copy", str(display), "--display-resolution", "400",
    )  # fmt: skip
    with rasterio.open(path) as copy:
        assert copy.res == (375, 375)
    outputs = last_step(steps_dir)["outputs"]
    assert outputs["display_resolution"] == [375, 375]
    assert outputs["display_resolution_requested"] == 400
    with pytest.raises(CSDRException, match="coarser"):
        convert(
            "--source", str(categorical), "--target-location", str(out),
            "--asset-name", "landcover", "--datetime", "2023",
            "--display-copy", str(display), "--display-resolution", "10",
        )  # fmt: skip


def test_display_copy_can_be_reprojected_while_cogs_keep_the_source_grid(
    tmp_path: Path, categorical: Path, steps_dir: Path
) -> None:
    out, display = tmp_path / "out", tmp_path / "display"
    convert(
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "landcover", "--datetime", "2023",
        "--display-copy", str(display), "--display-crs", "EPSG:3857",
    )  # fmt: skip
    path = display / "classes_display.tif"
    with rasterio.open(categorical) as source, rasterio.open(path) as copy:
        assert copy.crs.to_epsg() == 3857
        assert copy.width * copy.height < source.width * source.height
        assert copy.res[0] > 250  # coarser than 250 m and 10 x the source
        expected = rasterio.warp.transform_bounds(source.crs, copy.crs, *source.bounds)
        tolerance = 2 * max(copy.res)
        assert copy.bounds == pytest.approx(expected, abs=tolerance)
        assert copy.dtypes[0] == "uint16" and copy.nodata == NODATA
        assert copy.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        data = copy.read(1)
        assert set(np.unique(data)) <= {0, 1, 2, 3, NODATA}
        assert (data != NODATA).any()
    with rasterio.open(out / "cogs" / "classes_0_0.tif") as cog:
        assert cog.crs.to_epsg() == 3577 and cog.res == (RES, RES)
    step = last_step(steps_dir)
    assert step["outputs"]["display_crs"] == "EPSG:3857"
    assert any(
        "EPSG:3857 units (reprojected; the COGs are not)" in note
        for note in step["inputs"]["method_notes"]
    )
    with pytest.raises(CSDRException, match="needs --display-copy"):
        convert(
            "--source", str(categorical), "--target-location", str(out),
            "--asset-name", "landcover", "--datetime", "2023",
            "--display-crs", "EPSG:3857",
        )  # fmt: skip
    with pytest.raises(CSDRException, match="is not a CRS"):
        convert(
            "--source", str(categorical), "--target-location", str(out),
            "--asset-name", "landcover", "--datetime", "2023",
            "--display-copy", str(display), "--display-crs", "EPSG:not-a-code",
        )  # fmt: skip


def test_declared_sha256_is_verified_before_writing(
    tmp_path: Path, categorical: Path, steps_dir: Path
) -> None:
    out = tmp_path / "out"
    with pytest.raises(CSDRException, match="SHA-256 mismatch"):
        convert(
            "--source", str(categorical), "--target-location", str(out),
            "--asset-name", "a", "--datetime", "2023", "--source-sha256", "0" * 64,
        )  # fmt: skip
    assert not out.exists() or not any(out.rglob("*"))  # nothing written
    convert(
        "--source", str(categorical), "--target-location", str(out),
        "--asset-name", "a", "--datetime", "2023",
        "--source-sha256", sha256(categorical).upper(),
    )  # fmt: skip
    assert last_step(steps_dir)["inputs"]["source_sha256"] == sha256(categorical)


def test_store_urls_are_staged_and_written_through_the_store(
    tmp_path: Path, categorical: Path, class_dbf: Path, monkeypatch: pytest.MonkeyPatch,
    steps_dir: Path,
) -> None:  # fmt: skip
    buckets = {"in": tmp_path / "in-bucket", "out": tmp_path / "out-bucket"}
    for directory in buckets.values():
        directory.mkdir()
    (buckets["in"] / "classes.tif").write_bytes(categorical.read_bytes())
    (buckets["in"] / "classes.dbf").write_bytes(class_dbf.read_bytes())

    def store_for(url: str, **kwargs: object) -> LocalStore:
        if url.startswith("s3://"):
            bucket, _, prefix = url[len("s3://") :].partition("/")
            root = buckets[bucket] / prefix
            root.mkdir(parents=True, exist_ok=True)
            return LocalStore(root)
        return LocalStore(url, mkdir=True)

    monkeypatch.setattr("csdr.raster_stac.get_store_with_prefix_from_url", store_for)
    convert(
        "--source", "s3://in/classes.tif", "--target-location", "s3://out/run",
        "--asset-name", "landcover", "--datetime", "2023", "--tile-size", "20",
        "--classification-table", "s3://in/classes.dbf",
        "--display-copy", "s3://out/display",
    )  # fmt: skip
    written = buckets["out"] / "run"
    documents = items(written)
    assert len(documents) == 8
    assert documents["classes_0_0"]["assets"]["landcover"]["href"] == (
        "s3://out/run/cogs/classes_0_0.tif"
    )
    assert len(list((written / "cogs").glob("*.tif"))) == 8
    assert (buckets["out"] / "display" / "classes_display.tif").is_file()
    inputs = last_step(steps_dir)["inputs"]
    assert inputs["source_sha256"] == sha256(categorical)
    assert inputs["classification_table_sha256"] == sha256(class_dbf)


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--datetime", "yesterday"], "not an ISO 8601"),
        (["--datetime", "2023", "--band", "2"], "--band 2 is not in the source"),
        (
            ["--datetime", "2023", "--overview-resampling", "fancy"],
            "Unknown resampling",
        ),
    ],
)
def test_refusals(
    tmp_path: Path, categorical: Path, extra: list[str], message: str
) -> None:
    with pytest.raises(CSDRException, match=message):
        convert(
            "--source", str(categorical), "--target-location", str(tmp_path / "o"),
            "--asset-name", "a", *extra,
        )  # fmt: skip


def test_an_entirely_nodata_source_is_refused(tmp_path: Path) -> None:
    source = write_raster(
        tmp_path / "empty.tif", np.full((50, 50), NODATA, "uint16"), NODATA
    )
    with pytest.raises(CSDRException, match="Every tile is nodata"):
        convert(
            "--source", str(source), "--target-location", str(tmp_path / "o"),
            "--asset-name", "a", "--datetime", "2023",
        )  # fmt: skip

