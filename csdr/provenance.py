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

SUPPORTED_DATA_FORMATS = ["stac-geoparquet", "geoparquet", "parquet"]


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
    data_url: str,
    data_type: str,
    description: str = "",
    source_url: str | None = None,
    source_metadata_url: str | None = None,
    # Dataset doesn't pass an extra_info_dict, geometry does (including PMTiles url, and geometry run ID). Product probably does (incl. product run ID).
    extra_info_dict: dict[str, str | int] | None = None,
) -> dict[str, str | int]:
    """
    This function builds a provenance dictionary for a dataset, geometry, or product.
    It gathers metadata (like file size, etag, URLs, etc.) from the file at the given path (using the provided store), and returns a dictionary with all provenance info.
    It does not read from a database.
    """
    if data_type not in SUPPORTED_DATA_FORMATS:
        raise ValueError(
            f"Unsupported dataset type: {data_type}. Supported types are: {SUPPORTED_DATA_FORMATS}"
        )

    info = get_file_info(store, path)
    image_state = get_image_state()

    # Handle extra_info_dict being optional
    if extra_info_dict is None:
        extra_info_dict = {}
    provenance = {
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
        "provenanceUpdated": datetime.now(UTC).isoformat() + "Z",
        # Extra stuff! e.g. runId for the geometry run. To be expanded to dataset and product runId too I assume.
        **extra_info_dict,
    }

    if source_url is None:
        provenance["sourceUrl"] = data_url
    if source_metadata_url is not None:
        provenance["sourceMetadataUrl"] = source_metadata_url

    return provenance


def read_provenance(url: str) -> dict[str, str | int]:
    """
    This function reads a provenance JSON file from the given URL (which can be local, S3, or HTTP). It uses the appropriate store to fetch the file, loads the JSON content, and returns it as a Python dictionary.
    It does not read from a database, just from a file.
    """
    store = get_store_for_url(url)
    path = get_dataset_name_from_url(store, url)
    document = BytesIO(store.get(path).bytes())

    return load(document)
