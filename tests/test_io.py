from pathlib import Path
from urllib.parse import urlparse

import pytest
from obstore.store import HTTPStore, LocalStore, S3Store

from csdr.io import (
    get_file_name_from_url,
    get_store_with_prefix_from_url,
    get_url_from_store,
    # TODO: There are many more io.py functions to test
)


@pytest.mark.parametrize(
    "url,expected_store_type",
    [
        ("file:///Users/wj/Projects/csdr/csdr-cloud-spatial/README.md", "LocalStore"),
        ("s3://cool-bucket-name/path/to/blob.txt", "S3Store"),
        ("https://files.auspatious.com/#share/tide_models_clipped_indonesia.zip", "HTTPStore"),
        ("/tmp/file.txt", "LocalStore"),
    ],
)
def test_get_store_with_prefix_from_url(url: str, expected_store_type: str, aws_credentials: dict) -> None:
    store = get_store_with_prefix_from_url(url, mkdir=False)
    # Type check
    assert store.__class__.__name__ == expected_store_type
    # Property check
    if expected_store_type == "S3Store":
        assert f"s3://{store.config['bucket']}/{store.prefix}" == url
    elif expected_store_type == "HTTPStore":
        assert store.url == url
    elif expected_store_type == "LocalStore":
        assert str(store.prefix) == urlparse(url).path
    else:
        raise AssertionError(f"Unknown store type: {expected_store_type}")


def test_get_file_name_from_url() -> None:
    assert get_file_name_from_url("s3://bucket-name/prefix/to/file.txt") == "file.txt"
    assert get_file_name_from_url("s3://bucket-name/file.txt") == "file.txt"
    assert (
        get_file_name_from_url("s3://bucket-name/prefix/to/long.file.name.txt")
        == "long.file.name.txt"
    )
    assert (
        get_file_name_from_url("s3://bucket-name/prefix/to/file.parquet/file.txt")
        == "file.txt"
    )
    # Http and local paths
    assert get_file_name_from_url("https://example.com/path/to/file.txt") == "file.txt"
    assert get_file_name_from_url("/tmp/file.txt") == "file.txt"
    assert get_file_name_from_url("/tmp/path/to/file.txt") == "file.txt"


@pytest.mark.parametrize(
    "store,expected_url",
    [
        (get_store_with_prefix_from_url("s3://my-bucket/prefix/to/file.txt"), "s3://my-bucket/prefix/to/file.txt"),
        (get_store_with_prefix_from_url("https://example.com/data/file.txt"), "https://example.com/data/file.txt"),
        (get_store_with_prefix_from_url("file:///tmp/file.txt", mkdir=False), Path("/tmp/file.txt")),
    ],
)
def test_get_url_from_store(
    store: S3Store | HTTPStore | LocalStore, expected_url: str, aws_credentials: dict
) -> None:
    reconstructed_url = get_url_from_store(store)
    assert reconstructed_url == expected_url
