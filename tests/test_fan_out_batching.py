"""Fan-out batching: list-geometries-years --batch-size and process-geometry --geometry-ids."""

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box
from typer.testing import CliRunner

from csdr.cli_products import _template_process_geometry_command, products_app
from csdr.utils import CSDRException

runner = CliRunner()
INDICATORS = json.dumps({"sum-feature-area": {"indicator-name": "feature"}})
# Two 1 ha source features (EPSG:6933 equal-area construction). Each boundary
# holds whole features only, so the expected areas do not depend on clipping:
# one feature, both features, and none.
EXPECTED = {"one": 10_000.0, "both": 20_000.0, "outside": 0.0}


@pytest.fixture(autouse=True)
def steps_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    directory = tmp_path / "steps"
    monkeypatch.setenv("CSDR_PROVENANCE_STEPS_DIR", str(directory))
    monkeypatch.setenv(
        "IMAGE_REPO", "https://github.com/example/toolkit/tree/fixture-commit/"
    )
    return directory


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, str]:
    dataset = tmp_path / "dataset"
    (dataset / "partition").mkdir(parents=True)
    source = gpd.GeoDataFrame(
        geometry=[box(0, 0, 100, 100), box(300, 0, 400, 100)], crs="EPSG:6933"
    )
    source.to_parquet(dataset / "source.parquet")
    source.to_crs(4326).to_parquet(dataset / "partition" / "source_0_0.parquet")
    (dataset / "source.parquet.provenance.json").write_text(
        json.dumps(
            {"dataUrl": str(dataset / "source.parquet"), "dataType": "geoparquet"}
        )
    )
    boundaries = gpd.GeoDataFrame(
        {
            "csdr-id": ["one", "both", "outside"],
            "csdr-name": ["One", "Both", "Outside"],
        },
        geometry=[
            box(-50, -50, 150, 150),
            box(-50, -50, 450, 150),
            box(500, 500, 600, 600),
        ],
        crs="EPSG:6933",
    )
    boundaries.to_parquet(tmp_path / "boundaries.parquet")
    (tmp_path / "boundaries.parquet.provenance.json").write_text(
        json.dumps({"dataUrl": str(tmp_path / "boundaries.parquet")})
    )
    return {
        "geometry": str(tmp_path / "boundaries.parquet.provenance.json"),
        "dataset": str(dataset / "source.parquet.provenance.json"),
    }


def process(inputs: dict[str, str], target: Path, *ids: str) -> None:
    result = runner.invoke(
        products_app,
        [
            "process-geometry",
            "--product-id",
            "p",
            "--run-id",
            "r",
            "--geometry-provenance-url",
            inputs["geometry"],
            "--dataset-provenance-url",
            inputs["dataset"],
            "--indicators-to-extract",
            INDICATORS,
            "--datetime",
            "2024",
            "--target-location",
            str(target),
            *ids,
        ],
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result.exit_code == 0, result.output


def outputs(target: Path) -> dict[str, dict]:
    files = sorted((target / "feature" / "2024").glob("*.json"))
    return {path.name: json.loads(path.read_text()) for path in files}


def comparable(document: dict) -> dict:
    # The command line legitimately differs between a batch and single runs.
    document = json.loads(json.dumps(document))
    document["metadata"].pop("command")
    return document


def test_list_geometries_years_batches_ids_per_year(
    inputs: dict[str, str], tmp_path: Path
) -> None:
    args = [
        "list-geometries-years",
        "--geometry-provenance-url",
        inputs["geometry"],
        "--years",
        "2023,2024",
    ]
    single = runner.invoke(products_app, args)
    assert json.loads(single.stdout) == [
        {"year": year, "id": one_id}
        for year in ("2023", "2024")
        for one_id in ("one", "both", "outside")
    ]
    batched = runner.invoke(products_app, [*args, "--batch-size", "2"])
    assert json.loads(batched.stdout) == [
        {"year": "2023", "ids": "one,both"},
        {"year": "2023", "ids": "outside"},
        {"year": "2024", "ids": "one,both"},
        {"year": "2024", "ids": "outside"},
    ]
    out_file = tmp_path / "items.json"
    runner.invoke(
        products_app,
        [
            *args,
            "--batch-size",
            "3",
            "--exclude-ids",
            "both",
            "--out-file",
            str(out_file),
        ],
    )
    assert json.loads(out_file.read_text()) == [
        {"year": "2023", "ids": "one,outside"},
        {"year": "2024", "ids": "one,outside"},
    ]
    refused = runner.invoke(products_app, [*args, "--batch-size", "0"])
    assert refused.exit_code != 0


def test_batch_of_three_equals_three_singles(
    inputs: dict[str, str], tmp_path: Path
) -> None:
    singles, batch = tmp_path / "singles", tmp_path / "batch"
    for one_id in EXPECTED:
        process(inputs, singles, "--geometry-id", one_id)
    process(inputs, batch, "--geometry-ids", "one,both,outside")

    single_outputs, batch_outputs = outputs(singles), outputs(batch)
    assert sorted(batch_outputs) == ["p-both.json", "p-one.json", "p-outside.json"]
    assert {name: comparable(doc) for name, doc in batch_outputs.items()} == {
        name: comparable(doc) for name, doc in single_outputs.items()
    }
    for one_id, area in EXPECTED.items():
        value = batch_outputs[f"p-{one_id}.json"]["indicators"]["sum-feature-area"]
        assert value == pytest.approx(area, abs=0.05)


def test_resume_with_no_overwrite_skips_finished_ids(
    inputs: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "product"
    process(inputs, target, "--geometry-id", "one")
    finished = target / "feature" / "2024" / "p-one.json"
    finished.write_text(json.dumps({"sentinel": True}))

    processed = []
    import csdr.cli_products as cli_products

    real = cli_products.process_indicators_for_geometry

    def recording(*args: object, **kwargs: object) -> dict:
        processed.append(args[0].centroid.coords[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(cli_products, "process_indicators_for_geometry", recording)
    process(
        inputs, target, "--geometry-ids", "one,both,outside"
    )  # --no-overwrite is the default

    assert json.loads(finished.read_text()) == {"sentinel": True}
    assert len(processed) == 2
    assert sorted(outputs(target)) == ["p-both.json", "p-one.json", "p-outside.json"]

    # A fully finished batch reads nothing at all.
    monkeypatch.setattr(
        "csdr.cli_products.read_provenance",
        lambda url: pytest.fail("A finished batch must not read its inputs"),
    )
    process(inputs, target, "--geometry-ids", "one,both,outside")


@pytest.mark.parametrize(
    "ids,message",
    [
        ([], "exactly one of --geometry-id or --geometry-ids"),
        (["--geometry-id", "one", "--geometry-ids", "both"], "exactly one"),
        (["--geometry-ids", "one,,both"], "comma-separated list"),
        (["--geometry-ids", "one,one"], r"repeats ids: \['one'\]"),
    ],
)
def test_geometry_id_options_are_validated(
    inputs: dict[str, str], tmp_path: Path, ids: list[str], message: str
) -> None:
    with pytest.raises(CSDRException, match=message):
        process(inputs, tmp_path / "product", *ids)


def test_consolidate_reads_batched_outputs_and_templates_the_batch_command(
    inputs: dict[str, str],
    tmp_path: Path,
    steps_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "product"
    # The product JSON records the process command line from sys.argv.
    monkeypatch.setattr(
        "sys.argv",
        ["csdr", "products", "process-geometry", "--geometry-ids", "one,both,outside"],
    )
    process(inputs, target, "--geometry-ids", "one,both,outside")
    result = runner.invoke(
        products_app,
        [
            "consolidate",
            "--product-id",
            "p",
            "--location",
            str(target),
            "--geometry-provenance-url",
            inputs["geometry"],
            "--dataset-provenance-url",
            inputs["dataset"],
            "--indicator-name",
            "feature",
        ],
    )
    assert result.exit_code == 0, result.output
    frame = gpd.pd.read_parquet(target / "feature" / "p.parquet")
    assert sorted(frame["geometryOutputId"]) == ["both", "one", "outside"]
    summary = json.loads((steps_dir / "step-0000.json").read_text())
    assert summary["outputs"] == {"geometries_processed": 3}
    assert "--geometry-ids {geometry_ids}" in summary["command"]


PG = "csdr products process-geometry"
LOAD = '--load-kwargs={"resolution":100}'


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            f"--geometry-id=1 {LOAD} --datetime=2023",
            f"--geometry-id={{geometry_id}} {LOAD} --datetime={{datetime}}",
        ),
        (
            f"--geometry-id 1 {LOAD}",
            f"--geometry-id {{geometry_id}} {LOAD}",
        ),
        (
            f"--geometry-ids=1,2,3 {LOAD}",
            f"--geometry-ids={{geometry_ids}} {LOAD}",
        ),
        (
            f"--geometry-ids 1,2 {LOAD}",
            f"--geometry-ids {{geometry_ids}} {LOAD}",
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
    command = f"{PG} --geometry-id=1 {LOAD} --datetime=2023"
    (folder / "p-1.json").write_text(
        json.dumps(
            {
                "id": "x",
                "productId": "p",
                "productRunId": "r",
                "geometryOutputId": "1",
                "timePoint": "2023-01-01T00:00:00Z",
                "indicators": {"sum-x-area": 1.0},
                "metadata": {
                    "geometryProvenanceUrl": "g.json",
                    "datasetProvenanceUrl": "d.json",
                    "command": command,
                },
            }
        )
    )
    result = runner.invoke(
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
        f"{PG} --geometry-id={{geometry_id}} {LOAD} --datetime={{datetime}}"
    )
