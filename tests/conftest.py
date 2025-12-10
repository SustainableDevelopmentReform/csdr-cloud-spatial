import json
import os
from pathlib import Path

import boto3
import moto
import pytest
from geopandas import GeoDataFrame
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import LocalStore, S3Store
from odc.geo.geom import polygon
from pystac import ItemCollection

from csdr.utils import search_stacgeoparquet

DATA_DIR = Path(os.path.dirname(__file__), "data")
GEOPARQUET_FILE = Path("gmw/gmw.parquet")
GEOPARQUET_PATH = DATA_DIR / GEOPARQUET_FILE


@pytest.fixture
def sample_polygon() -> polygon:
    with open(DATA_DIR / "single_geometry.geojson") as f:
        geom = polygon(
            json.load(f)["features"][0]["geometry"]["coordinates"][0], crs="EPSG:4326"
        )
        return geom


@pytest.fixture
def sample_polygon2() -> polygon:
    with open(DATA_DIR / "single_geometry2.geojson") as f:
        geom = polygon(
            json.load(f)["features"][0]["geometry"]["coordinates"][0], crs="EPSG:4326"
        )
        return geom


# Can't use gmw.parquet here because it errors "rustac.RustacError: External error: General error: Invalid byte order"
# TODO: Consolidate test data to reduce data.
@pytest.fixture
def sample_stacgeoparquet(sample_polygon2: polygon) -> ItemCollection:
    return search_stacgeoparquet(str(DATA_DIR / "dep_s2_seagrass.parquet"), sample_polygon2, "2022")


@pytest.fixture
def local_testdata_obstore() -> LocalStore:
    return LocalStore(DATA_DIR)


@pytest.fixture
def geoparquet_relative() -> Path:
    return str(GEOPARQUET_FILE)


@pytest.fixture
def sample_gdf() -> GeoDataFrame:
    gdf = GeoDataFrame.from_file(DATA_DIR / "single_geometry.geojson")
    return gdf


@pytest.fixture
def aws_credentials() -> None:
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = (
        "us-east-1"  # Use us-east-1 to avoid LocationConstraint issues
    )


@pytest.fixture
def s3_client(aws_credentials: None) -> boto3.client:
    with moto.mock_aws():
        yield boto3.client("s3", region_name=os.environ["AWS_DEFAULT_REGION"])


# S3 test data, s3_testdata_obstore, s3_geoparquet_relative
@pytest.fixture
def s3_testdata_obstore(aws_credentials: None, s3_client: boto3.client) -> S3Store:
    """Create a moto S3 server and S3Store that works with obstore"""
    import threading
    import time

    from moto.server import DomainDispatcherApplication, create_backend_app, run_simple

    # Start moto server
    port = 5555
    endpoint_url = f"http://127.0.0.1:{port}"

    def start_server() -> None:
        application = DomainDispatcherApplication(create_backend_app)
        run_simple(
            "127.0.0.1",
            port,
            application,
            threaded=True,
            use_reloader=False,
            use_debugger=False,
        )

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    time.sleep(1)  # Give server time to start

    bucket_name = "test-bucket"
    s3_client.create_bucket(Bucket=bucket_name)

    # Upload test data to the mock S3 bucket
    for root, _, files in os.walk(DATA_DIR):
        for file in files:
            file_path = Path(root) / file
            s3_key = str(file_path.relative_to(DATA_DIR))
            s3_client.upload_file(str(file_path), bucket_name, s3_key)

    return S3Store(
        bucket_name,
        credential_provider=Boto3CredentialProvider(
            session=boto3.Session(
                aws_access_key_id="testing",
                aws_secret_access_key="testing",
                region_name="us-east-1",
            )
        ),
        endpoint=endpoint_url,
        region="us-east-1",
        allow_http=True,
    )
