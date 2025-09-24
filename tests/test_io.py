import pytest

from csdr.io import (
    get_dataset_name_from_url,
    get_file_name_from_url,
    get_prefix,
    get_store_for_url,
    get_url_from_store_filename,
)


def test_get_prefix() -> None:
    assert get_prefix("s3://bucket-name/prefix/to/file.txt") == "prefix/to"
    assert get_prefix("s3://bucket-name/file.txt") is None
    assert get_prefix("s3://bucket-name/prefix/to/") == "prefix/to"
    assert get_prefix("s3://bucket-name/prefix/to") == "prefix/to"
    assert get_prefix("s3://bucket-name/") is None
    assert get_prefix("s3://bucket-name") is None
    assert get_prefix("s3://bucket-name/prefix.to/file.txt") == "prefix.to"
    assert get_prefix("s3://bucket-name/prefix.to/file.name.txt") == "prefix.to"
    assert (
        get_prefix("s3://bucket-name/long/prefix/with/lots/of/parts/file.csv")
        == "long/prefix/with/lots/of/parts"
    )


def test_get_file_name_from_url() -> None:
    assert get_file_name_from_url("s3://bucket-name/prefix/to/file.txt") == "file.txt"
    assert get_file_name_from_url("s3://bucket-name/file.txt") == "file.txt"
    assert (
        get_file_name_from_url("s3://bucket-name/prefix/to/long.file.name.txt")
        == "long.file.name.txt"
    )
    assert get_file_name_from_url("s3://bucket-name/prefix/to/") is None
    assert get_file_name_from_url("s3://bucket-name/prefix/to") is None
    assert get_file_name_from_url("s3://bucket-name/") is None
    assert get_file_name_from_url("s3://bucket-name") is None
    assert get_file_name_from_url("https://example.com/path/to/file.txt") == "file.txt"
    assert get_file_name_from_url("https://example.com/") is None
    assert get_file_name_from_url("file:///local/path/to/file.txt") == "file.txt"
    assert get_file_name_from_url("file:///local/path/to/") is None
    assert get_file_name_from_url("file:///local/path/to") is None


@pytest.mark.parametrize(
    "url,expected_url",
    [
        ("s3://my-bucket/prefix/to/file.txt", "s3://my-bucket/prefix/to/file.txt"),
        ("https://example.com/data/file.txt", "https://example.com/data/file.txt"),
        ("/tmp/file.txt", "/tmp/file.txt"),
    ],
)
def test_get_url_from_store_filename(
    url: str, expected_url: str, aws_credentials: dict
) -> None:
    store = get_store_for_url(url, mkdir=False)
    filename = get_dataset_name_from_url(store, url)
    assert filename is not None
    reconstructed_url = get_url_from_store_filename(store, filename)
    assert reconstructed_url == expected_url
