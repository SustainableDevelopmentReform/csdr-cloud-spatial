import json
from pathlib import Path
from typing import Any

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
