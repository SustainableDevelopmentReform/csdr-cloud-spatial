import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import HTTPStore, LocalStore, S3Store


def exists(store: HTTPStore | S3Store | LocalStore, path: str) -> bool:
    try:
        store.head(path)
    except FileNotFoundError:
        return False
    return True


def get_file_info(store: HTTPStore | S3Store | LocalStore, path: str) -> dict[str, Any]:
    info = store.head(path)
    return {
        "size": info["size"],
        "e_tag": info["e_tag"],
        "last_modified": info.get("last_modified", None),
    }


def write_json(
    store: HTTPStore | S3Store | LocalStore, path: str, data: dict[str, Any]
) -> None:
    if type(store) is HTTPStore:
        raise ValueError("Cannot write to HTTPStore")
    elif type(store) is LocalStore:
        # No put, so do it a boring way
        full_path = Path(store.prefix) / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    else:
        # S3Store has put
        store.put(
            path,
            json.dumps(data, indent=2).encode("utf-8"),
            attributes={"Content-Type": "application/json"},
        )


def get_store_for_url(
    url: str, mkdir: bool = False
) -> HTTPStore | S3Store | LocalStore:
    if url.startswith("s3://"):
        s3_url = urlparse(url)
        bucket = s3_url.netloc
        return S3Store(bucket, credential_provider=Boto3CredentialProvider())
    elif url.startswith("http://") or url.startswith("https://"):
        return HTTPStore()
    else:
        the_path = Path(url)
        # Ensure the directory exists
        if mkdir:
            if the_path.suffix:  # It's a file path
                the_path.parent.mkdir(parents=True, exist_ok=True)
            else:  # It's a directory path
                the_path.mkdir(parents=True, exist_ok=True)

        # LocalStore expects the directory, not a file
        path_prefix = the_path if not the_path.suffix else the_path.parent
        return LocalStore(prefix=path_prefix)


def get_file_name_from_url(url: str) -> str:
    parsed_url = urlparse(url)
    return Path(parsed_url.path).name


def get_s3_prefix(s3_url_str: str) -> str:
    s3_url = urlparse(s3_url_str)
    file_name = get_file_name_from_url(s3_url_str)
    if s3_url.path.endswith(file_name):
        return s3_url.path.lstrip("/").replace(file_name, "").rstrip("/")
    else:
        return s3_url.path.lstrip("/").rstrip("/")
