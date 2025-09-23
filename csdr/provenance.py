import os
from datetime import UTC, datetime

from obstore.store import HTTPStore, LocalStore, S3Store

from csdr.io import get_file_info
from csdr.utils import make_uuid


def get_image_state() -> dict[str, str]:
    """Get the image state from environment variables"""
    return {
        "image_repo": os.getenv("IMAGE_REPO", "not-set"),
        "image_tag": os.getenv("IMAGE_TAG", "not-set"),
    }


def get_dataset_provenance(
    name: str,
    store: HTTPStore | S3Store | LocalStore,
    path: str,
    source_url: str,
    source_metadata_url: str,
    file_url: str,
    file_type: str,
    uuid: str | None = None,
    extra_info_dict: dict[str, str | int] | None = None,
) -> dict[str, str | int]:
    info = get_file_info(store, path)
    image_state = get_image_state()

    # TODO:
    # - Add dates. Date added/updated/etc.

    return {
        "name": name,
        "uuid": uuid or make_uuid(name),
        "data_path": path,
        "data_size": info["size"],
        "data_etag": info["e_tag"].strip('"'),
        "image_repo": image_state["image_repo"],
        "image_tag": image_state["image_tag"],
        "source_url": source_url,
        "source_metadata_url": source_metadata_url,
        "file_url": file_url,
        "file_type": file_type,
        "provenance_updated": datetime.now(UTC).isoformat() + "Z",
        **(extra_info_dict or {}),
    }
