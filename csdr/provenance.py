import os
from datetime import UTC, datetime

from obstore.store import HTTPStore, LocalStore, S3Store

from csdr.io import get_file_info
from csdr.utils import make_uuid

SUPPORTED_DATASET_FORMATS = [
    "stac-geoparquet",
    "geoparquet",
]


def get_image_state() -> dict[str, str]:
    """Get the image state from environment variables"""
    return {
        "image_code": os.getenv("IMAGE_REPO", "not-set"),
        "image_tag": os.getenv("IMAGE_TAG", "not-set"),
    }


def get_provenance(
    id: str,
    store: HTTPStore | S3Store | LocalStore,
    path: str,
    source_url: str,
    source_metadata_url: str,
    dataset_url: str,
    dataset_type: str,
    uuid: str | None = None,
    extra_info_dict: dict[str, str | int] | None = None,
) -> dict[str, str | int]:
    if dataset_type not in SUPPORTED_DATASET_FORMATS:
        raise ValueError(
            f"Unsupported dataset type: {dataset_type}. Supported types are: {SUPPORTED_DATASET_FORMATS}"
        )

    info = get_file_info(store, path)
    image_state = get_image_state()

    # TODO:
    # - Add dates. Date added/updated/etc.

    return {
        "id": id,
        "uuid": uuid or make_uuid(id),
        "data_size": info["size"],
        "data_etag": info["e_tag"].strip('"'),
        "image_code": image_state["image_code"],
        "image_tag": image_state["image_tag"],
        "source_url": source_url,
        "source_metadata_url": source_metadata_url,
        "dataset_url": dataset_url,
        "dataset_type": dataset_type,
        "provenance_updated": datetime.now(UTC).isoformat() + "Z",
        **(extra_info_dict or {}),
    }
