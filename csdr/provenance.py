import inspect
import json
import logging
import os
import sys
from datetime import UTC, datetime
from io import BytesIO
from json import load
from pathlib import Path

from obstore.store import ObjectStore

from csdr.io import (
    get_file_info,
    get_store_with_prefix_from_url,
    get_url_from_store,
    split_path_and_file_name_from_url,
)
from csdr.utils import CSDRException

logger = logging.getLogger(__name__)
SUPPORTED_DATA_FORMATS = ["stac-geoparquet", "geoparquet", "parquet"]

PROVENANCE_STEPS_DIR = Path("/tmp/provenance-steps")


def _get_github_url(file: str, line: int) -> str | None:
    """Build a GitHub permalink to the caller's source line using the IMAGE_REPO env var."""
    image_repo = os.getenv("IMAGE_REPO", None)
    if image_repo is None or image_repo == "unknown":
        return None
    # IMAGE_REPO is e.g. https://github.com/.../tree/<commit>/
    # We need /blob/<commit>/path#Lline
    base_url = image_repo.rstrip("/").replace("/tree/", "/blob/")
    # Find the relative path within the repo (everything after last csdr/)
    marker = "csdr/"
    idx = file.rfind(marker)
    if idx == -1:
        return None
    relative_path = file[idx:]
    return f"{base_url}/{relative_path}#L{line}"


def write_step(
    label: str,
    inputs: dict | None = None,
    outputs: dict | None = None,
) -> None:
    """Write a provenance step JSON file for the current CLI command.

    Call this at the end of any ``csdr`` command that should appear in the
    workflow provenance.  The caller's file and line are captured
    automatically via :func:`inspect.stack`.
    """
    caller = inspect.stack()[1]

    source: dict[str, str | int] = {
        "file": caller.filename,
        "line": caller.lineno,
        "function": caller.function,
    }

    github_url = _get_github_url(caller.filename, caller.lineno)
    if github_url is not None:
        source["github"] = github_url
    else:
        raise CSDRException(
            "GitHub URL for provenance step is not available. Make sure the COMMIT environment variable is set to a valid commit hash, and that the caller file is within the csdr/ directory of the repository."
        )

    step = {
        "label": label,
        "command": " ".join(sys.argv),
        "inputs": inputs or {},
        "outputs": outputs or {},
        "completed_at": datetime.now(UTC).isoformat() + "Z",
        "source": source,
    }

    step_dir = PROVENANCE_STEPS_DIR
    step_dir.mkdir(parents=True, exist_ok=True)

    # Name files by count so ordering is preserved
    existing_count = len(list(step_dir.glob("step-*.json")))
    step["order"] = existing_count
    step_file = step_dir / f"step-{existing_count:04d}.json"

    step_file.write_text(json.dumps(step, indent=2))
    logger.info(f"Wrote provenance step to {step_file}")


# Read steps and clear steps are needed for local work. In Argo, steps are in individual pods which get cleaned up so they aren't needed.
def read_steps() -> list[dict]:
    """Read all provenance steps from the steps directory, in order."""
    if not PROVENANCE_STEPS_DIR.exists():
        return []
    files = sorted(PROVENANCE_STEPS_DIR.glob("step-*.json"))
    steps = []
    for f in files:
        try:
            steps.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            logger.warning(f"Could not read provenance step file {f}")
    return steps


def clear_steps() -> None:
    """Remove all provenance step files. Call after reading steps into provenance."""
    if not PROVENANCE_STEPS_DIR.exists():
        return
    for f in PROVENANCE_STEPS_DIR.glob("step-*.json"):
        f.unlink()
    logger.info("Cleared provenance step files")


def get_image_state() -> dict[str, str]:
    """Get the image state from environment variables"""
    image_code = os.getenv("IMAGE_REPO", None)
    image_tag = os.getenv("IMAGE_TAG", None)
    if (
        image_code is None
        or image_tag is None
        or image_code == "unknown"
        or image_tag == "unknown"
    ):
        raise CSDRException(
            "IMAGE_REPO and IMAGE_TAG environment variables must be set for provenance."
        )
    return {
        "imageCode": image_code,
        "imageTag": image_tag,
    }


def get_provenance(
    id: str,
    store: ObjectStore,
    file_name: str,
    data_url: str,
    data_type: str,
    description: str = "",
    source_url: str | None = None,
    source_metadata_url: str | None = None,
    workflow_dag: list | None = None,
    # Dataset can pass an extra_info_dict with dataPmtilesUrl, geometry does (including PMTiles url, and geometry run ID). Product probably does (incl. product run ID).
    extra_info_dict: dict[str, str | int] | None = None,
) -> dict[str, str | int]:
    """
    This function builds a provenance dictionary for a dataset, geometry, or product.
    It gathers metadata (like file size, etag, URLs, etc.) from the file at the given path (using the provided store), and returns a dictionary with all provenance info.
    It does not read from a database.
    """
    if data_type not in SUPPORTED_DATA_FORMATS:
        raise CSDRException(
            f"Unsupported dataset type: {data_type}. Supported types are: {SUPPORTED_DATA_FORMATS}"
        )

    info = get_file_info(store, file_name)
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
        "provenanceUrl": f"{get_url_from_store(store)}/{file_name}.provenance.json",
        # These three get removed from the dict if posting to database
        "provenanceUpdated": datetime.now(UTC).isoformat() + "Z",
        "workflowDag": workflow_dag,
        # Extra stuff! e.g. geometriesRunId and productRunId
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
    path, file_name = split_path_and_file_name_from_url(url)
    store = get_store_with_prefix_from_url(path)
    document = BytesIO(store.get(file_name).bytes())

    return load(document)
