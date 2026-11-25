"""parquet_source: private objects staged with credentials, prefixes and public reads unsigned."""

from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import boto3
import geopandas as gpd
import pytest
import sedona.db
from botocore import UNSIGNED
from botocore.response import StreamingBody
from botocore.stub import Stubber
from shapely.geometry import box

from csdr import parquet_source as parquet_source_module
from csdr.parquet_source import parquet_source, sedona_options
from csdr.utils import CSDRException

CREDENTIAL_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_SECURITY_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_ARN",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
)


@pytest.fixture(autouse=True)
def static_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate the boto3 chain from the host: static test keys, no profiles or IMDS."""
    for name in CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")


@pytest.fixture
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    monkeypatch.setenv("AWS_REGION", "us-west-2")


def client_for(monkeypatch: pytest.MonkeyPatch) -> object:
    client = boto3.client(
        "s3",
        region_name="ap-southeast-2",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        aws_session_token="temporary-test-token",
    )
    monkeypatch.setattr("csdr.parquet_source.boto3.client", lambda service: client)
    return client


def expect_download(stub: Stubber, content: bytes) -> None:
    stub.add_response(
        "head_object",
        {"ContentLength": len(content), "ETag": '"revision"', "VersionId": "v1"},
        {"Bucket": "private-bucket", "Key": "Agency/Source.parquet"},
    )
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(BytesIO(content), len(content))},
        {
            "Bucket": "private-bucket",
            "Key": "Agency/Source.parquet",
            "IfMatch": '"revision"',
            "VersionId": "v1",
        },
    )


def clipped_area_m2(sd: object, readable: str, wkt: str) -> float:
    """Clipped EPSG:6933 area of a source read the way the engines read it."""
    sd.read_parquet(readable, options=sedona_options(readable)).to_view(
        "dataset", overwrite=True
    )
    boundary = f"ST_SetSRID(ST_GeomFromText('{wkt}'), 4326)"
    return float(
        sd.sql(
            f"""
            SELECT SUM(ST_Area(ST_Transform(ST_Intersection(geometry, {boundary}), 6933))) AS area
            FROM dataset WHERE ST_Intersects(geometry, {boundary})
            """
        ).to_pandas()["area"][0]
    )


def test_private_staging_pins_version_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Stubber(client_for(monkeypatch)) as stub:
        expect_download(stub, b"parquet")
        with (
            pytest.raises(RuntimeError, match="reader failed"),
            parquet_source("s3://private-bucket/Agency/Source.parquet") as name,
        ):
            saved = Path(name)
            assert saved.read_bytes() == b"parquet"
            assert saved.stat().st_mode & 0o777 == 0o600
            raise RuntimeError("reader failed")
        assert not saved.exists()
        stub.assert_no_pending_responses()


def test_authenticated_failure_never_falls_back_to_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Stubber(client_for(monkeypatch)) as stub:
        stub.add_client_error("head_object", service_error_code="AccessDenied")
        with (
            pytest.raises(CSDRException, match="Check AWS credentials"),
            parquet_source("s3://private-bucket/Agency/Source.parquet"),
        ):
            pytest.fail("Access denied must not produce a source")


def test_limit_prevents_download(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Mock()
    client.head_object.return_value = {"ContentLength": 20, "ETag": "revision"}
    monkeypatch.setattr("csdr.parquet_source.boto3.client", lambda service: client)
    with (
        pytest.raises(CSDRException, match="staging limit"),
        parquet_source("s3://private-bucket/Agency/Source.parquet", max_bytes=10),
    ):
        pytest.fail("Oversized input must not be read")
    client.get_object.assert_not_called()


def test_staging_limit_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    client.head_object.return_value = {"ContentLength": 20, "ETag": "revision"}
    monkeypatch.setattr("csdr.parquet_source.boto3.client", lambda service: client)
    monkeypatch.setenv("CSDR_PARQUET_SOURCE_MAX_BYTES", "10")
    with (
        pytest.raises(CSDRException, match="staging limit"),
        parquet_source("s3://private-bucket/Agency/Source.parquet"),
    ):
        pytest.fail("Oversized input must not be read")
    client.get_object.assert_not_called()
    monkeypatch.setenv("CSDR_PARQUET_SOURCE_MAX_BYTES", "0")
    with (
        pytest.raises(CSDRException, match="must be positive"),
        parquet_source("s3://private-bucket/Agency/Source.parquet"),
    ):
        pytest.fail("A zero limit must be refused")


def test_private_download_is_measured_by_real_sedona(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.parquet"
    gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs=4326).to_parquet(source)
    with Stubber(client_for(monkeypatch)) as stub:
        expect_download(stub, source.read_bytes())
        with parquet_source("s3://private-bucket/Agency/Source.parquet") as readable:
            assert sedona_options(readable) == {}  # a local staged file
            area = clipped_area_m2(sedona.db.connect(), readable, box(0, 0, 0.5, 1).wkt)
    expected = gpd.GeoSeries([box(0, 0, 0.5, 1)], crs=4326).to_crs(6933).area[0]
    assert area == pytest.approx(expected, abs=0.02)


# Prefixes and anonymous reads (regression from staging every S3 URL).


def test_credentials_probe_follows_the_isolated_chain(
    no_credentials: None,
) -> None:
    assert parquet_source_module.credentials_available() is False


def test_partition_prefix_passes_through_without_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "csdr.parquet_source.boto3.client",
        lambda *a, **kw: pytest.fail("A prefix must not be staged"),
    )
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    url = "s3://public-data/datasets/aca/0-0-1/partition/"
    with parquet_source(url) as readable:
        assert readable == url
        assert sedona_options(readable) == {
            "aws.skip_signature": True,
            "aws.region": "us-east-1",
        }


def test_partition_prefix_without_region_env_uses_the_default_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "csdr.parquet_source.boto3.client",
        lambda *a, **kw: pytest.fail("A prefix must not be staged"),
    )
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    url = "s3://public-data/datasets/aca/0-0-1/partition/"
    with parquet_source(url) as readable:
        assert readable == url
        assert sedona_options(readable) == {
            "aws.skip_signature": True,
            "aws.region": "ap-southeast-2",
        }


def test_key_without_object_but_with_children_is_read_as_a_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Stubber(client_for(monkeypatch)) as stub:
        stub.add_client_error(
            "head_object", service_error_code="404", http_status_code=404
        )
        stub.add_response(
            "list_objects_v2",
            {"KeyCount": 1, "Contents": [{"Key": "Agency/partition/a.parquet"}]},
            {"Bucket": "private-bucket", "Prefix": "Agency/partition/", "MaxKeys": 1},
        )
        with parquet_source("s3://private-bucket/Agency/partition") as readable:
            assert readable == "s3://private-bucket/Agency/partition/"
        stub.assert_no_pending_responses()


def test_missing_object_without_children_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Stubber(client_for(monkeypatch)) as stub:
        stub.add_client_error(
            "head_object", service_error_code="404", http_status_code=404
        )
        stub.add_response(
            "list_objects_v2",
            {"KeyCount": 0},
            {
                "Bucket": "private-bucket",
                "Prefix": "Agency/Missing.parquet/",
                "MaxKeys": 1,
            },
        )
        with (
            pytest.raises(CSDRException, match="Check AWS credentials"),
            parquet_source("s3://private-bucket/Agency/Missing.parquet"),
        ):
            pytest.fail("A missing object must not produce a source")


def test_local_staging_refuses_a_prefix() -> None:
    with (
        pytest.raises(CSDRException, match="single S3 object"),
        parquet_source("s3://private-bucket/Agency/partition/", local=True),
    ):
        pytest.fail("A prefix cannot be hashed as one file")


def test_without_credentials_public_objects_stream_unsigned(
    no_credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "csdr.parquet_source.boto3.client",
        lambda *a, **kw: pytest.fail("An anonymous read must not be staged"),
    )
    url = "s3://public-data/Agency/Source.parquet"
    with parquet_source(url) as readable:
        assert readable == url
        assert sedona_options(readable) == {
            "aws.skip_signature": True,
            "aws.region": "us-west-2",
        }


def test_without_credentials_local_staging_uses_an_unsigned_client(
    no_credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = []
    real_client = boto3.client
    stubbers = []

    def recording_client(*args: object, **kwargs: object) -> object:
        made.append(kwargs)
        client = real_client(*args, **kwargs)
        stub = Stubber(client)
        stub.add_response(
            "head_object",
            {"ContentLength": 7, "ETag": '"public"', "VersionId": "v9"},
            {"Bucket": "public-data", "Key": "Agency/Source.parquet"},
        )
        # Anonymous principals rarely hold s3:GetObjectVersion: pin by ETag only.
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(BytesIO(b"parquet"), 7)},
            {
                "Bucket": "public-data",
                "Key": "Agency/Source.parquet",
                "IfMatch": '"public"',
            },
        )
        stub.activate()
        stubbers.append(stub)
        return client

    monkeypatch.setattr("csdr.parquet_source.boto3.client", recording_client)
    with parquet_source("s3://public-data/Agency/Source.parquet", local=True) as staged:
        assert Path(staged).read_bytes() == b"parquet"
    assert made == [{"region_name": "us-west-2", "config": made[0]["config"]}]
    assert made[0]["config"].signature_version is UNSIGNED
    stubbers[0].assert_no_pending_responses()


def test_partition_folder_is_measured_across_files_by_real_sedona(
    tmp_path: Path,
) -> None:
    partition = tmp_path / "partition"
    partition.mkdir()
    gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs=4326).to_parquet(
        partition / "reefextent_a.parquet"
    )
    gpd.GeoDataFrame(geometry=[box(1, 0, 2, 1)], crs=4326).to_parquet(
        partition / "reefextent_b.parquet"
    )
    url = f"{partition}/"
    with parquet_source(url) as readable:
        assert readable == url  # local paths pass through
        area = clipped_area_m2(sedona.db.connect(), readable, box(0.5, 0, 1.5, 1).wkt)
    expected = gpd.GeoSeries([box(0.5, 0, 1.5, 1)], crs=4326).to_crs(6933).area[0]
    assert area == pytest.approx(expected, abs=0.05)
