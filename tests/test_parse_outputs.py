"""parse_outputs: absent indicator values are skipped and row metadata passes through."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from csdr.products import parse_outputs


def consolidated(tmp_path: Path, rows: list[dict]) -> pd.DataFrame:
    # Round-trip through Parquet as consolidate does: struct fields are unioned
    # across rows, so a key missing from one row reads back as null.
    path = tmp_path / "product.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return pd.read_parquet(path)


def test_absent_values_are_skipped_not_posted_as_nan(tmp_path: Path) -> None:
    frame = consolidated(
        tmp_path,
        [
            {
                "geometryOutputId": "a",
                "timePoint": "2023-01-01T00:00:00Z",
                "indicators": {"sum-x-area": 10.0, "mean-y": 0.5},
            },
            {
                "geometryOutputId": "b",
                "timePoint": "2023-01-01T00:00:00Z",
                "indicators": {"sum-x-area": 0.0},
            },
        ],
    )
    outputs = parse_outputs(frame)
    assert outputs["sum-x-area"]["2023-01-01T00:00:00Z"] == [
        {"geometryOutputId": "a", "value": 10.0},
        {"geometryOutputId": "b", "value": 0.0},  # a real zero is kept
    ]
    assert outputs["mean-y"]["2023-01-01T00:00:00Z"] == [
        {"geometryOutputId": "a", "value": 0.5}
    ]


@pytest.mark.parametrize("absent", [None, float("nan")])
def test_none_and_nan_values_are_skipped_in_memory(absent: object) -> None:
    frame = pd.DataFrame(
        [
            {
                "geometryOutputId": "a",
                "timePoint": "2023",
                "indicators": {"p50-y": absent, "p10-y": 0.1},
            }
        ]
    )
    assert parse_outputs(frame) == {
        "p10-y": {"2023": [{"geometryOutputId": "a", "value": 0.1}]}
    }


def test_row_metadata_is_passed_through_as_plain_json(tmp_path: Path) -> None:
    metadata = {
        "geometryProvenanceUrl": "g.provenance.json",
        "rasterCellRule": "cell-centre",
        "percentileRange": [0.0, 1.0],
        "zonalCellCounts": {"y": {"boundaryCells": 9, "validCells": 4}},
    }
    frame = consolidated(
        tmp_path,
        [
            {
                "geometryOutputId": "a",
                "timePoint": "2023",
                "indicators": {"mean-y": 0.25},
                "metadata": metadata,
            }
        ],
    )
    # Parquet hands lists back as NumPy arrays.
    assert isinstance(frame.iloc[0]["metadata"]["percentileRange"], np.ndarray)
    output = parse_outputs(frame)["mean-y"]["2023"][0]
    assert output == {"geometryOutputId": "a", "value": 0.25, "metadata": metadata}
    json.dumps(output, allow_nan=False)  # postable as strict JSON


def test_rows_without_metadata_have_no_metadata_key() -> None:
    frame = pd.DataFrame(
        [{"geometryOutputId": "a", "timePoint": "2023", "indicators": {"x": 1.0}}]
    )
    output = parse_outputs(frame)["x"]["2023"][0]
    assert output == {"geometryOutputId": "a", "value": 1.0}
    assert not math.isnan(output["value"])
