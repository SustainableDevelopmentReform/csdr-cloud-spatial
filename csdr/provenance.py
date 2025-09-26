import os
from datetime import UTC, datetime
from io import BytesIO
from json import load

from obstore.store import HTTPStore, LocalStore, S3Store

from csdr.io import (
    get_dataset_name_from_url,
    get_file_info,
    get_store_for_url,
    get_url_from_store_filename,
)

SUPPORTED_DATA_FORMATS = [
    "stac-geoparquet",
    "geoparquet",
]


def get_image_state() -> dict[str, str]:
    """Get the image state from environment variables"""
    return {
        "imageCode": os.getenv("IMAGE_REPO", "not-set"),
        "imageTag": os.getenv("IMAGE_TAG", "not-set"),
    }


def get_provenance(
    id: str,
    store: HTTPStore | S3Store | LocalStore,
    path: str,
    source_url: str,
    source_metadata_url: str,
    data_url: str,
    data_type: str,
    description: str = "",
    extra_info_dict: dict[str, str | int] | None = None,
) -> dict[str, str | int]:
    if data_type not in SUPPORTED_DATA_FORMATS:
        raise ValueError(
            f"Unsupported dataset type: {data_type}. Supported types are: {SUPPORTED_DATA_FORMATS}"
        )

    info = get_file_info(store, path)
    image_state = get_image_state()

    return {
        "id": id,
        "dataType": data_type,
        "dataEtag": info["e_tag"].strip('"'),
        "dataSize": info["size"],
        "dataUrl": data_url,
        "description": description,
        "imageCode": image_state["imageCode"],
        "imageTag": image_state["imageTag"],
        # This should be the URL to this file itself
        "provenanceUrl": get_url_from_store_filename(store, path) + ".provenance.json",
        # These three get removed from the dict if posting to database
        "sourceUrl": source_url,
        "sourceMetadataUrl": source_metadata_url,
        "provenanceUpdated": datetime.now(UTC).isoformat() + "Z",
        # Extra stuffs?!
        **(extra_info_dict or {}),
    }


def read_provenance(url: str) -> dict[str, str | int]:
    store = get_store_for_url(url)
    path = get_dataset_name_from_url(store, url)
    document = BytesIO(store.get(path).bytes())

    return load(document)
