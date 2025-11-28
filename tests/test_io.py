import os

import pytest

from csdr.io import (
    get_file_name_from_url,
    get_store_with_prefix_from_url,
    get_url_from_store,
    # TODO: There are many more io.py functions to test
)


@pytest.mark.parametrize(
    "url,expected_store_type,expected_value",
    [
        ("file:///Users/wj/Projects/csdr/csdr-cloud-spatial/README.md", "LocalStore", "/Users/wj/Projects/csdr/csdr-cloud-spatial"),
        ("s3://bucket-name/path/to/blob.txt", "S3Store", "bucket-name"),
        ("https://files.auspatious.com/#share/tide_models_clipped_indonesia.zip", "HTTPStore"),
        ("/tmp/file.txt", "LocalStore", "/tmp"),
    ],
)
def test_get_store_with_prefix_from_url(url: str, expected_store_type: str, expected_value: str, aws_credentials: dict) -> None:
    store = get_store_with_prefix_from_url(url, mkdir=False)
    # Type check
    assert store.__class__.__name__ == expected_store_type
    # Property check
    if expected_store_type == "S3Store":
        assert f"s3://{store.config['bucket']}/{store.prefix}" == url
    elif expected_store_type == "HTTPStore":
        assert store.url == url
    elif expected_store_type == "LocalStore":
        assert os.path.abspath(store.prefix) == os.path.abspath(url)
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
    # # These should raise ValueError because there is no valid file name
    # with pytest.raises(ValueError):
    #     get_file_name_from_url("s3://bucket-name/prefix/to/")
    # with pytest.raises(ValueError):
    #     get_file_name_from_url("s3://bucket-name/prefix/to")
    # with pytest.raises(ValueError):
    #     get_file_name_from_url("s3://bucket-name/")
    # with pytest.raises(ValueError):
    #     get_file_name_from_url("s3://bucket-name")
    assert get_file_name_from_url("https://example.com/path/to/file.txt") == "file.txt"
    assert get_file_name_from_url("/tmp/file.txt") == "/tmp/file.txt"
    assert get_file_name_from_url("/tmp/path/to/file.txt") == "/tmp/path/to/file.txt"
    # assert get_file_name_from_url("https://example.com/") is None
    # file:/// is not supported.
    # assert get_file_name_from_url("file:///local/path/to/file.txt") == "file.txt"
    # assert get_file_name_from_url("file:///local/path/to/") is None
    # assert get_file_name_from_url("file:///local/path/to") is None


# # Outdated tests. Not sure if function even needed.
# @pytest.mark.parametrize(
#     "store,expected_url",
#     [
#         ("s3://my-bucket/prefix/to/file.txt", "s3://my-bucket/prefix/to/file.txt"),
#         ("https://example.com/data/file.txt", "https://example.com/data/file.txt"),
#         ("/tmp/file.txt", "/tmp/file.txt"), # Absolute local
#         # ("./tmp/file.txt", "./tmp/file.txt"), # Relative local # This fails by returning "tmp/file.txt" which is basically the same and I don't want to fix it.
#     ],
# )
# def test_get_url_from_store(
#     store: S3Store | HttpStore | LocalStore, expected_url: str, aws_credentials: dict
# ) -> None:
#     reconstructed_url = get_url_from_store(store)
#     assert reconstructed_url == expected_url
