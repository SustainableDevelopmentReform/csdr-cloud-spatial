import os

import pytest
from obstore.store import LocalStore, S3Store

from csdr.io import write_json
from csdr.provenance import get_image_state, get_provenance

PROVENANCE_DETAILS = {
    # "source_url": "https://example.com/source",
    # "source_metadata_url": "https://example.com/source/metadata",
    "data_url": "s3://example.com/file",
    "data_type": "stac-geoparquet",
}

TEST_GEOPARQUET_NAME = "example-global-test-geoparquet"


def _test_provenance_fields(provenance: dict[str, str | int]) -> None:
    for key, value in PROVENANCE_DETAILS.items():
        key = "".join(
            part.capitalize() if i > 0 else part
            for i, part in enumerate(key.split("_"))
        )
        if key == "extraInfo":
            for extra_key, extra_value in value.items():
                assert provenance[extra_key] == extra_value
        else:
            assert provenance[key] == value


def test_write_provenance(
    local_testdata_obstore: LocalStore,
    s3_testdata_obstore: S3Store,
    geoparquet_relative: str,
) -> None:
    """Test writing provenance information to both local and S3 stores"""
    provenance_dict = get_provenance(
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

    provenance = get_provenance(
        TEST_GEOPARQUET_NAME,
        local_testdata_obstore,
        geoparquet_relative,
        **PROVENANCE_DETAILS,
    )

    assert isinstance(provenance["dataSize"], int) and provenance["dataSize"] == 15836
    assert isinstance(provenance["dataEtag"], str)
    assert provenance["imageCode"] in [
        "not-set",
        "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial/tree/fake-sha-commit/",
    ]
    assert provenance["imageTag"] in [
        "not-set",
        "https://fake-registry/csdr/csdr-cloud-spatial:test",
    ]
    _test_provenance_fields(provenance)


# Test getting provenance information from a mocked S3 file
@pytest.mark.skip(
    "Skipping because moto doesn't set the bucket name in the store config"
)
def test_s3_dataset_provenance(
    s3_testdata_obstore: S3Store, geoparquet_relative: str
) -> None:
    """Test getting provenance information from a mocked S3 file"""

    provenance = get_provenance(
        TEST_GEOPARQUET_NAME,
        s3_testdata_obstore,
        geoparquet_relative,
        **PROVENANCE_DETAILS,
    )

    assert isinstance(provenance["dataSize"], int) and provenance["dataSize"] == 15836
    assert (
        isinstance(provenance["dataEtag"], str)
        and provenance["dataEtag"] == "255ab95f6888079345b27aa2e1547796"  # Why quoted?!
    )
    assert provenance["imageCode"] in [
        "not-set",
        "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial/tree/fake-sha-commit/",
    ]
    assert provenance["imageTag"] in [
        "not-set",
        "https://fake-registry/csdr/csdr-cloud-spatial:test",
    ]

    _test_provenance_fields(provenance)


def test_get_image_state() -> None:
    """Test getting provenance information from environment variables"""
    os.environ["IMAGE_REPO"] = "test-repo"
    os.environ["IMAGE_TAG"] = "abc-123"

    image_provenance = get_image_state()

    assert image_provenance["imageCode"] == "test-repo"
    assert image_provenance["imageTag"] == "abc-123"

    # Clean up environment variables
    del os.environ["IMAGE_REPO"]
    del os.environ["IMAGE_TAG"]


# Skip if on GitHub Actions
@pytest.mark.skip("Skipping because Docker isn't available most places")
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
        image_code, image_tag = output

        assert (
            image_code
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
