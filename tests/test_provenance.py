import os

import pytest
from obstore.store import LocalStore, S3Store

from csdr.io import write_json
from csdr.provenance import get_dataset_provenance, get_image_state

PROVENANCE_DETAILS = {
    "source_url": "https://example.com/source",
    "source_metadata_url": "https://example.com/source/metadata",
    "dataset_url": "s3://example.com/file",
    "dataset_type": "stacgeoparquet",
}

TEST_GEOPARQUET_NAME = "example-global-test-geoparquet"


def test_write_provenance(
    local_testdata_obstore: LocalStore,
    s3_testdata_obstore: S3Store,
    geoparquet_relative: str,
) -> None:
    """Test writing provenance information to both local and S3 stores"""
    provenance_dict = get_dataset_provenance(
        TEST_GEOPARQUET_NAME,
        local_testdata_obstore,
        geoparquet_relative,
        **PROVENANCE_DETAILS,
    )

    # Write to local store
    local_prov_path = geoparquet_relative.replace(".parquet", ".provenance.json")
    write_json(local_testdata_obstore, local_prov_path, provenance_dict)
    assert local_testdata_obstore.head(local_prov_path)["size"] > 0

    # Write to S3 store
    s3_prov_path = geoparquet_relative.replace(".parquet", ".provenance.json")
    write_json(s3_testdata_obstore, s3_prov_path, provenance_dict)
    assert s3_testdata_obstore.head(s3_prov_path)["size"] > 0


# Test getting provenance information from a local file
def test_local_dataset_provenance(
    local_testdata_obstore: LocalStore, geoparquet_relative: str
) -> None:
    """Test getting provenance information from a local file"""

    provenance = get_dataset_provenance(
        TEST_GEOPARQUET_NAME,
        local_testdata_obstore,
        geoparquet_relative,
        **PROVENANCE_DETAILS,
    )

    assert provenance["data_path"] == geoparquet_relative
    assert isinstance(provenance["data_size"], int) and provenance["data_size"] == 15836
    assert isinstance(provenance["data_etag"], str)
    assert provenance["image_repo"] in [
        "not-set",
        "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial/tree/fake-sha-commit/",
    ]
    assert provenance["image_tag"] in [
        "not-set",
        "https://fake-registry/csdr/csdr-cloud-spatial:test",
    ]
    for key, value in PROVENANCE_DETAILS.items():
        if key == "extra_info":
            for extra_key, extra_value in value.items():
                assert provenance[extra_key] == extra_value
        else:
            assert provenance[key] == value


# Test getting provenance information from a mocked S3 file
def test_s3_dataset_provenance(
    s3_testdata_obstore: S3Store, geoparquet_relative: str
) -> None:
    """Test getting provenance information from a mocked S3 file"""

    provenance = get_dataset_provenance(
        TEST_GEOPARQUET_NAME,
        s3_testdata_obstore,
        geoparquet_relative,
        **PROVENANCE_DETAILS,
    )

    assert provenance["data_path"] == str(geoparquet_relative)
    assert isinstance(provenance["data_size"], int) and provenance["data_size"] == 15836
    assert (
        isinstance(provenance["data_etag"], str)
        and provenance["data_etag"]
        == "255ab95f6888079345b27aa2e1547796"  # Why quoted?!
    )
    assert provenance["image_repo"] in [
        "not-set",
        "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial/tree/fake-sha-commit/",
    ]
    assert provenance["image_tag"] in [
        "not-set",
        "https://fake-registry/csdr/csdr-cloud-spatial:test",
    ]

    for key, value in PROVENANCE_DETAILS.items():
        if key == "extra_info":
            for extra_key, extra_value in value.items():
                assert provenance[extra_key] == extra_value
        else:
            assert provenance[key] == value


def test_get_image_state() -> None:
    """Test getting provenance information from environment variables"""
    os.environ["IMAGE_REPO"] = "test-repo"
    os.environ["IMAGE_TAG"] = "abc-123"

    image_provenance = get_image_state()

    assert image_provenance["image_repo"] == "test-repo"
    assert image_provenance["image_tag"] == "abc-123"

    # Clean up environment variables
    del os.environ["IMAGE_REPO"]
    del os.environ["IMAGE_TAG"]


# Skip if on GitHub Actions
@pytest.mark.skipif(
    os.getenv("GITHUB_ACTIONS") == "true", reason="Skip test on GitHub Actions"
)
def test_docker_image_state() -> None:
    """Test getting provenance information from the docker image"""
    import subprocess

    try:
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "csdr-cloud-spatial:latest",
                "python3",
                "-c",
                "import os; print(os.getenv('IMAGE_REPO')); print(os.getenv('IMAGE_TAG'))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        output = result.stdout.strip().split("\n")
        assert len(output) == 2
        image_repo, image_tag = output

        assert (
            image_repo
            == "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial/tree/unknown-commit/"
        )
        assert image_tag == "unknown"
    except FileNotFoundError:
        print("Docker not found, skipping docker image provenance test.")
    except subprocess.CalledProcessError as e:
        print(f"Error running docker command: {e}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        assert False, "Docker command failed"
    except Exception as e:
        print(f"Unexpected error: {e}")
        assert False, "Unexpected error occurred"
