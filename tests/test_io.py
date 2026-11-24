from pathlib import Path
from urllib.parse import urlparse

import pytest

from csdr.io import (
    get_store_with_prefix_from_url,
    get_url_from_store,
    split_path_and_file_name_from_url,
    # TODO: There are many more io.py functions to test
)


@pytest.mark.parametrize(
    "url,expected_store_type",
    [
        # These tests work locally but fail in CI due to lack of local file access
        # ("file:///tmp/file.txt", "LocalStore"),
        # ("/tmp/file.txt", "LocalStore"),
        ("s3://bucket-name/path/to/blob.txt", "S3Store"),
        (
            "https://files.auspatious.com/#share/tide_models_clipped_indonesia.zip",
            "HTTPStore",
        ),
    ],
)
def test_get_store_with_prefix_from_url(
    url: str, expected_store_type: str, aws_credentials: dict
) -> None:
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


def test_split_path_and_file_name_from_url() -> None:
    assert split_path_and_file_name_from_url("s3://bucket-name/prefix/to/file.txt") == (
        "s3://bucket-name/prefix/to",
        "file.txt",
    )
    assert split_path_and_file_name_from_url("s3://bucket-name/file.txt") == (
        "s3://bucket-name",
        "file.txt",
    )
    assert split_path_and_file_name_from_url(
        "s3://bucket-name/prefix/to/long.file.name.txt"
    ) == ("s3://bucket-name/prefix/to", "long.file.name.txt")
    assert split_path_and_file_name_from_url(
        "s3://bucket-name/prefix/to/file.parquet/file.txt"
    ) == ("s3://bucket-name/prefix/to/file.parquet", "file.txt")
    # Http and local paths
    assert split_path_and_file_name_from_url(
        "https://example.com/path/to/file.txt"
    ) == ("https://example.com/path/to", "file.txt")
    assert split_path_and_file_name_from_url("/tmp/file.txt") == ("/tmp", "file.txt")
    assert split_path_and_file_name_from_url("/tmp/path/to/file.txt") == (
        "/tmp/path/to",
        "file.txt",
    )


@pytest.mark.parametrize(
    "url,expected_url",
    [
        ("s3://bucket-name/prefix/to/file.txt", "s3://bucket-name/prefix/to/file.txt"),
        ("https://example.com/data/file.txt", "https://example.com/data/file.txt"),
        # This test works locally but fails in CI due to lack of local file access
        # ("file:///tmp/file.txt", Path("/tmp/file.txt")),
    ],
)
def test_get_url_from_store(url: str, expected_url: str, aws_credentials: dict) -> None:
    store = get_store_with_prefix_from_url(url, mkdir=False)
    reconstructed_url = get_url_from_store(store)
    assert reconstructed_url == expected_url


@pytest.mark.parametrize(
    "url,expected_url",
    [
        # Object keys are case-sensitive: the prefix must not be lower-cased.
        (
            "s3://bucket-name/Products/Mixed-Case/Run-A",
            "s3://bucket-name/Products/Mixed-Case/Run-A",
        ),
        # Only the scheme is normalised; a trailing slash is still removed.
        (
            "S3://bucket-name/Products/Mixed-Case/",
            "s3://bucket-name/Products/Mixed-Case",
        ),
        (
            "HTTPS://example.com/Data/File.Parquet",
            "https://example.com/Data/File.Parquet",
        ),
    ],
)
def test_get_store_preserves_case(
    url: str, expected_url: str, aws_credentials: dict
) -> None:
    store = get_store_with_prefix_from_url(url, mkdir=False)
    assert get_url_from_store(store) == expected_url


def test_get_store_preserves_case_in_local_paths(tmp_path: Path) -> None:
    target = tmp_path / "Mixed" / "Case-Dir"
    target.mkdir(parents=True)

    for url in (str(target), f"file://{target}", f"FILE://{target}/"):
        store = get_store_with_prefix_from_url(url)
        assert str(store.prefix) == str(target)
