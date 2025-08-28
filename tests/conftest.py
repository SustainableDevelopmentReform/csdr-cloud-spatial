import json
import os

import pytest
from odc.geo.geom import polygon
from pystac import ItemCollection

from csdr.utils import open_stacgeoparquet

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


@pytest.fixture
def sample_polygon() -> dict:
    with open(os.path.join(DATA_DIR, "single_geometry.geojson")) as f:
        geom = polygon(
            json.load(f)["features"][0]["geometry"]["coordinates"][0], crs="EPSG:4326"
        )
        return geom


@pytest.fixture
def sample_stacgeoparquet() -> ItemCollection:
    return open_stacgeoparquet(os.path.join(DATA_DIR, "gmw", "gmw.parquet"))
