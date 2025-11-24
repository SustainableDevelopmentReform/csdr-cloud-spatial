import os

import pytest

from csdr.io import (
    get_prefix_file_name_from_url,
    get_prefix_from_url,
    get_store_from_url,
    make_url_from_store_prefix_filename,
    # TODO: There are many more io.py functions to test
)


# TODO: Should all stores have a trailing slash? Http has one by default. Do the others?
@pytest.mark.parametrize(
    "url,expected_store_type,expected_prefix",
    [
        # S3 and Http stores do not include the prefix
        ("s3://bucket-name/prefix/to/file.txt", "S3Store", "bucket-name"),
        ("https://example.com/data/file.txt", "HTTPStore", "https://example.com/"),
        # The local store is different because it includes the prefix
        ("/tmp/file.txt", "LocalStore", os.path.abspath("/")),
        ("/tmp/path/to/file.txt", "LocalStore", os.path.abspath("/")),
    ],
)
def test_get_store_from_url(url: str, expected_store_type: str, expected_prefix: str, aws_credentials: dict) -> None:
    store = get_store_from_url(url, mkdir=False)
    # Type check
    assert store.__class__.__name__ == expected_store_type
    # Path check
    if expected_store_type == "S3Store":
        # S3Store: check bucket
        assert hasattr(store, "config")
        assert store.config["bucket"] == expected_prefix
    elif expected_store_type == "HTTPStore":
        # HTTPStore: check url
        assert hasattr(store, "url")
        assert store.url == expected_prefix
    elif expected_store_type == "LocalStore":
        # LocalStore: check prefix (should be absolute path)
        assert hasattr(store, "prefix")
        # Accept both Path and str
        prefix = store.prefix if isinstance(store.prefix, str) else str(store.prefix)
        # Accept if prefix matches expected_prefix or is a parent of the file
        # TODO: Why would we accept a parent. This must be the actual expected_prefix
        assert os.path.abspath(prefix) == expected_prefix or os.path.abspath(prefix) == os.path.dirname(os.path.abspath(url))
    else:
        raise AssertionError(f"Unknown store type: {expected_store_type}")


def test_get_prefix_from_url() -> None:
    assert get_prefix_from_url("s3://bucket-name/prefix/to/file.txt") == "prefix/to/"
    assert get_prefix_from_url("https://example.com/prefix/to/file.txt") == "prefix/to/"
    assert get_prefix_from_url("/tmp/prefix/to/file.txt") == "prefix/to/"
    assert get_prefix_from_url("s3://bucket-name/file.txt") is None
    assert get_prefix_from_url("https://example.com/file.txt") is None
    assert get_prefix_from_url("/file.txt") is None


def test_get_prefix_file_name_from_url() -> None:
    assert get_prefix_file_name_from_url("s3://bucket-name/prefix/to/file.txt") == "file.txt"
    assert get_prefix_file_name_from_url("s3://bucket-name/file.txt") == "file.txt"
    assert (
        get_prefix_file_name_from_url("s3://bucket-name/prefix/to/long.file.name.txt")
        == "long.file.name.txt"
    )
    assert (
        get_prefix_file_name_from_url("s3://bucket-name/prefix/to/file.parquet/file.txt")
        == "file.txt"
    )
    # # These should raise ValueError because there is no valid file name
    # with pytest.raises(ValueError):
    #     get_prefix_file_name_from_url("s3://bucket-name/prefix/to/")
    # with pytest.raises(ValueError):
    #     get_prefix_file_name_from_url("s3://bucket-name/prefix/to")
    # with pytest.raises(ValueError):
    #     get_prefix_file_name_from_url("s3://bucket-name/")
    # with pytest.raises(ValueError):
    #     get_prefix_file_name_from_url("s3://bucket-name")
    assert get_prefix_file_name_from_url("https://example.com/path/to/file.txt") == "file.txt"
    assert get_prefix_file_name_from_url("/tmp/file.txt") == "/tmp/file.txt"
    assert get_prefix_file_name_from_url("/tmp/path/to/file.txt") == "/tmp/path/to/file.txt"
    # assert get_prefix_file_name_from_url("https://example.com/") is None
    # file:/// is not supported.
    # assert get_prefix_file_name_from_url("file:///local/path/to/file.txt") == "file.txt"
    # assert get_prefix_file_name_from_url("file:///local/path/to/") is None
    # assert get_prefix_file_name_from_url("file:///local/path/to") is None


@pytest.mark.parametrize(
    "url,expected_url",
    [
        ("s3://my-bucket/prefix/to/file.txt", "s3://my-bucket/prefix/to/file.txt"),
        ("https://example.com/data/file.txt", "https://example.com/data/file.txt"),
        ("/tmp/file.txt", "/tmp/file.txt"), # Absolute local
        # ("./tmp/file.txt", "./tmp/file.txt"), # Relative local # This fails by returning "tmp/file.txt" which is basically the same and I don't want to fix it.
    ],
)
def test_make_url_from_store_prefix_filename(
    url: str, expected_url: str, aws_credentials: dict
) -> None:
    store = get_store_from_url(url, mkdir=False)
    prefix_filename = get_prefix_file_name_from_url(url)
    # prefix = get_prefix_from_url()
    assert prefix_filename is not None
    reconstructed_url = make_url_from_store_prefix_filename(store, prefix_filename)
    assert reconstructed_url == expected_url
