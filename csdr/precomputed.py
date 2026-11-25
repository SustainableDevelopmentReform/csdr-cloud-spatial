"""Project pinned pre-computed JSON values into ordinary SDF product rows.

No analysis, allocation, aggregation, catalogue writes or main-run promotion.
Bindings name each value explicitly. A missing value is an error; an explicit
null is retained in the receipt and omitted from numerical outputs.

A Parquet source is projected into the same JSON-pointer space: ``/rows/<index>/
<column>`` (rows in file order) or, with key columns, ``/rows/<key>/<column>``.
``bind_regions`` generates the per-region bindings skeleton from a geometries run.
"""

import hashlib
import json
import math
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from csdr.catalogue import CatalogueReader, digest
from csdr.io import get_store_with_prefix_from_url, split_path_and_file_name_from_url
from csdr.utils import CSDRException

MAX_BYTES = 32 * 1024**2
MAX_BINDINGS = 100_000
MAX_SAFE_INTEGER = 2**53 - 1
SPEC_FIELDS = {
    "version",
    "productId",
    "productRunId",
    "datasetRunId",
    "geometriesRunId",
    "sourceSha256",
    "role",
    "method",
    "reviewStatement",
    "caveats",
    "sourceProvenancePointers",
    "bindings",
}
SOURCE_FORMATS = ("json", "parquet")
KEY_SOURCES = ("metadata", "name", "csdr-id")
# bind-regions writes these where the operator must still decide a value.
# Exactly the values bind-regions writes. Only these are refused, so free text
# that happens to contain braces (e.g. "{{workflow.name}}") stays valid.
TEMPLATE_PLACEHOLDERS = frozenset(
    "{{" + name + "}}"
    for name in (
        "productId",
        "productRunId",
        "datasetRunId",
        "sourceSha256",
        "role",
        "method.name",
        "method.version",
        "method.description",
        "reviewStatement",
        "caveat",
        "sourceProvenancePointer",
        "indicatorId",
        "timePoint",
        "unit",
        "evidenceClass",
        "temporalBasis",
    )
)
COLUMN_PLACEHOLDER = "{{column}}"


def _unreplaced(spec: dict) -> bool:
    """True when a value is still a bind-regions placeholder."""

    def strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [text for item in value for text in strings(item)]
        if isinstance(value, dict):
            return [text for item in value.values() for text in strings(item)]
        return []

    if any(text in TEMPLATE_PLACEHOLDERS for text in strings(spec)):
        return True
    bindings = spec.get("bindings")
    return isinstance(bindings, list) and any(
        isinstance(binding, dict)
        and isinstance(binding.get("valuePointer"), str)
        and binding["valuePointer"].endswith("/" + COLUMN_PLACEHOLDER)
        for binding in bindings
    )


BINDING_FIELDS = {
    "geometryOutputId",
    "indicatorId",
    "timePoint",
    "valuePointer",
    "unit",
    "evidenceClass",
    "temporalBasis",
}


def _fail(message: str) -> None:
    raise CSDRException(message)


def _text(value: object, label: str, limit: int = 2000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(f"Supply a bounded, non-empty {label}.")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("Duplicate JSON keys make the source ambiguous.")
        result[key] = value
    return result


def read_json(path: Path) -> tuple[object, str]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        _fail("Partition JSON larger than 32 MiB before preparing product outputs.")
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: _fail("Non-finite JSON numbers are not allowed."),
        )
    except (ValueError, UnicodeDecodeError, RecursionError):
        _fail("The source must be valid, bounded JSON.")
    return document, hashlib.sha256(raw).hexdigest()


def pointer(document: object, path: str) -> object:
    """Resolve RFC 6901 tokens without evaluating expressions or coercing keys."""
    if (
        not isinstance(path, str)
        or len(path) > 2000
        or (path and not path.startswith("/"))
    ):
        _fail("Use a bounded JSON Pointer for every selected value.")
    value = document
    for part in path.split("/")[1:] if path else []:
        if re.search(r"~(?![01])", part):
            _fail("Invalid JSON Pointer escape.")
        token = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif (
            isinstance(value, list)
            and re.fullmatch(r"0|[1-9][0-9]*", token)
            and int(token) < len(value)
        ):
            value = value[int(token)]
        else:
            _fail(
                "A selected JSON Pointer does not exist. Missing data cannot become zero."
            )
    return value


def validate_spec(spec: dict) -> None:
    if (
        not isinstance(spec, dict)
        or set(spec) != SPEC_FIELDS
        or type(spec["version"]) is not int
        or spec["version"] != 1
    ):
        _fail(
            "Use the complete version 1 pre-computed specification without unknown fields."
        )
    if _unreplaced(spec):
        _fail("Replace every {{...}} placeholder from the bindings template first.")
    for field in ("productId", "productRunId", "datasetRunId", "geometriesRunId"):
        _text(spec[field], field, 200)
    if not isinstance(spec["sourceSha256"], str) or not re.fullmatch(
        r"[a-f0-9]{64}", spec["sourceSha256"]
    ):
        _fail("Pin the source file's SHA-256 before preparing outputs.")
    if spec["role"] not in {"measurement", "context", "illustrative"}:
        _fail("Declare measurement, context or illustrative use.")
    method = spec["method"]
    if not isinstance(method, dict) or set(method) != {
        "name",
        "version",
        "description",
    }:
        _fail("Describe the original method and its version.")
    for field, value in method.items():
        _text(value, f"method {field}")
    _text(spec["reviewStatement"], "review statement")
    if not isinstance(spec["caveats"], list) or not 1 <= len(spec["caveats"]) <= 100:
        _fail("Retain the source caveats.")
    for caveat in spec["caveats"]:
        _text(caveat, "caveat", 5000)
    if (
        not isinstance(spec["sourceProvenancePointers"], list)
        or not 1 <= len(spec["sourceProvenancePointers"]) <= 100
    ):
        _fail("Identify the source's provenance fields with JSON Pointers.")
    for path in spec["sourceProvenancePointers"]:
        _text(path, "source provenance pointer")
    if (
        not isinstance(spec["bindings"], list)
        or not 1 <= len(spec["bindings"]) <= MAX_BINDINGS
    ):
        _fail("Supply between one and 100,000 explicit value bindings.")
    seen = set()
    units = {}
    for binding in spec["bindings"]:
        if not isinstance(binding, dict) or set(binding) != BINDING_FIELDS:
            _fail("Use complete value bindings without unknown fields.")
        for key, value in binding.items():
            _text(value, key, 2000 if key in {"valuePointer", "temporalBasis"} else 200)
        try:
            if not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z",
                binding["timePoint"],
            ):
                raise ValueError
            dt = datetime.fromisoformat(binding["timePoint"])
            if (
                not binding["timePoint"].endswith("Z")
                or dt.utcoffset().total_seconds() != 0
            ):
                raise ValueError
            canonical_time = dt.astimezone(UTC).isoformat()
        except (ValueError, AttributeError):
            _fail(
                "Use an explicit UTC time point ending in Z, with its temporal basis."
            )
        identity = (binding["geometryOutputId"], binding["indicatorId"], canonical_time)
        if identity in seen:
            _fail(
                "Duplicate geometry, indicator and time-point bindings are not allowed."
            )
        seen.add(identity)
        indicator = binding["indicatorId"]
        if indicator in units and units[indicator] != binding["unit"]:
            _fail("An indicator cannot have conflicting units.")
        units[indicator] = binding["unit"]


def _json_value(value: object) -> object:
    """Parquet cell to a JSON value without numeric coercion.

    Numbers, strings, booleans and nulls are kept as they are (only numbers can be
    bound). Temporal values become ISO strings, decimals their exact text, bytes
    hexadecimal; nested lists and structs are converted element by element.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _key_token(value: object, column: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if type(value) is int:
        return str(value)
    _fail(
        f"Key column {column!r} must hold non-blank strings or integers (found {type(value).__name__})."
    )


def read_parquet_source(
    path: Path, key_columns: list[str] | None = None
) -> tuple[dict, str]:
    """Project a pinned Parquet file into the JSON-pointer space; return it and its SHA-256.

    ``/rows`` is the list of rows in file order (``/rows/<index>/<column>``), or with
    ``key_columns`` an object keyed by those columns' values, one level per column
    (``/rows/<key>/<column>``, ``/rows/<key1>/<key2>/<column>``). Keys must be
    unique. ``/schema/columns``, ``/schema/metadata/<key>`` and ``/rowCount``
    describe the file, for ``sourceProvenancePointers``.
    """
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        _fail("Partition Parquet larger than 32 MiB before preparing product outputs.")
    try:
        table = pq.read_table(pa.BufferReader(raw))
    except (pa.ArrowException, OSError):
        _fail("The source must be a readable Parquet file.")
    names = table.column_names
    if len(set(names)) != len(names):
        _fail("Parquet column names must be unique.")
    rows = [
        {key: _json_value(value) for key, value in row.items()}
        for row in table.to_pylist()
    ]
    metadata = {
        key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
        for key, value in (table.schema.metadata or {}).items()
    }
    document = {
        "rows": rows,
        "schema": {"columns": names, "metadata": metadata},
        "rowCount": table.num_rows,
    }
    if key_columns:
        missing = [column for column in key_columns if column not in names]
        if missing or len(set(key_columns)) != len(key_columns):
            _fail(f"Key columns must be distinct columns of the source: {missing}")
        keyed: dict = {}
        for row in rows:
            tokens = [_key_token(row[column], column) for column in key_columns]
            node = keyed
            for token in tokens[:-1]:
                node = node.setdefault(token, {})
            if tokens[-1] in node:
                _fail(
                    f"Key {'/'.join(tokens)!r} occurs more than once; bindings would be ambiguous."
                )
            node[tokens[-1]] = row
        document["rows"] = keyed
    return document, hashlib.sha256(raw).hexdigest()


def project_values(document: object, spec: dict) -> tuple[list[dict], list[dict]]:
    validate_spec(spec)
    specification_hash = digest(spec)
    rows = {}
    nulls = []
    for binding in spec["bindings"]:
        value = pointer(document, binding["valuePointer"])
        if value is None:
            nulls.append(binding)
            continue
        if (
            type(value) not in {int, float}
            or abs(value) > MAX_SAFE_INTEGER
            or not math.isfinite(value)
        ):
            _fail(
                "Selected values must be finite JSON numbers within the API's safe numeric range."
            )
        key = (binding["geometryOutputId"], binding["timePoint"])
        if key not in rows:
            rows[key] = {
                "id": str(
                    uuid5(
                        NAMESPACE_URL,
                        digest(
                            {
                                "productRunId": spec["productRunId"],
                                "sourceSha256": spec["sourceSha256"],
                                "geometryOutputId": key[0],
                                "timePoint": key[1],
                            }
                        ),
                    )
                ),
                "productId": spec["productId"],
                "productRunId": spec["productRunId"],
                "geometryOutputId": key[0],
                "timePoint": key[1],
                "indicators": {},
                "metadata": {
                    "precomputed": {
                        "version": 1,
                        "sourceSha256": spec["sourceSha256"],
                        "specificationSha256": specification_hash,
                        "role": spec["role"],
                        "method": spec["method"],
                        "caveats": spec["caveats"],
                        "reviewStatement": spec["reviewStatement"],
                        "bindings": [],
                    }
                },
            }
        row = rows[key]
        row["indicators"][binding["indicatorId"]] = value
        row["metadata"]["precomputed"]["bindings"].append(binding)
    if not rows:
        _fail("All selected values are null. No numerical product was prepared.")
    return [rows[key] for key in sorted(rows)], nulls


def _related(record: dict, key: str) -> str | None:
    value = record.get(key)
    return value.get("id") if isinstance(value, dict) else None


def verify_catalogue(spec: dict, reader: CatalogueReader) -> dict:
    """Identity/units checks, not a licence or scientific approval decision."""
    product = reader.read("product", spec["productId"])
    dataset = reader.read("dataset-run", spec["datasetRunId"])
    geometry = reader.read("geometries-run", spec["geometriesRunId"])
    if reader.read("product-run", spec["productRunId"], missing_ok=True) is not None:
        _fail("Choose a fresh product run ID. Existing runs must not be overwritten.")
    if (
        reader.organization_id
        and product.get("organizationId") != reader.organization_id
    ):
        _fail("The product does not belong to the selected organisation.")
    if (
        not _related(product, "dataset")
        or not _related(product, "geometries")
        or _related(product, "dataset") != _related(dataset, "dataset")
        or _related(product, "geometries") != _related(geometry, "geometries")
    ):
        _fail("The product and input runs do not form the same catalogue chain.")
    provenance = dataset.get("provenanceJson")
    declaration = (
        provenance.get("precomputedSource") if isinstance(provenance, dict) else None
    )
    if (
        not isinstance(declaration, dict)
        or declaration.get("version") != 1
        or declaration.get("sourceSha256") != spec["sourceSha256"]
        or declaration.get("role") != spec["role"]
    ):
        _fail(
            "The dataset run must declare the same version 1 precomputedSource hash and role."
        )
    records = [product, dataset, geometry]
    for identifier in sorted({b["geometryOutputId"] for b in spec["bindings"]}):
        record = reader.read("geometry-output", identifier)
        if _related(record, "geometriesRun") != spec["geometriesRunId"]:
            _fail("A selected geometry output belongs to a different boundary run.")
        records.append(record)
    for identifier, unit in sorted(
        {b["indicatorId"]: b["unit"] for b in spec["bindings"]}.items()
    ):
        record = reader.read("indicator/measured", identifier)
        if record.get("unit") != unit:
            _fail("An indicator's unit differs from its source binding.")
        records.append(record)
    return {
        "status": "catalogue-inputs-matched",
        "snapshotSha256": digest({"records": records}),
        "checkedAt": datetime.now(UTC).isoformat(),
        "recordCount": len(records),
    }


def prepare_precomputed(
    source: Path,
    spec_file: Path,
    out: Path,
    *,
    offline: bool = False,
    source_format: str = "json",
    key_columns: list[str] | None = None,
) -> dict:
    if out.exists():
        _fail(
            "Choose a new output directory. Historical preparations are not overwritten."
        )
    if source_format not in SOURCE_FORMATS:
        _fail(f"The source format must be one of {list(SOURCE_FORMATS)}.")
    if key_columns and source_format != "parquet":
        _fail("Key columns apply to Parquet sources only.")
    spec, _ = read_json(spec_file)
    validate_spec(spec)
    if source_format == "parquet":
        document, source_hash = read_parquet_source(source, key_columns)
    else:
        document, source_hash = read_json(source)
    if source_hash != spec["sourceSha256"]:
        _fail("The source bytes differ from the pinned SHA-256. No output was written.")
    reader = None if offline else CatalogueReader.from_environment()
    before = verify_catalogue(spec, reader) if reader else None
    source_provenance = {
        p: pointer(document, p) for p in spec["sourceProvenancePointers"]
    }
    rows, nulls = project_values(document, spec)
    after = verify_catalogue(spec, reader) if reader else None
    if before and before["snapshotSha256"] != after["snapshotSha256"]:
        _fail("Catalogue inputs changed during preparation. Start a fresh run.")
    check = after or {"status": "not-verified"}
    if before:
        check["checkedBeforePreparationAt"] = before["checkedAt"]
    for row in rows:
        row["metadata"]["precomputed"]["catalogueVerification"] = check
    receipt = {
        "version": 1,
        "operation": "project-precomputed-values",
        "specification": spec,
        "sourceProvenance": source_provenance,
        "sourceSha256": source_hash,
        "specificationSha256": digest(spec),
        "catalogueVerification": check,
        "rowCount": len(rows),
        "valueCount": sum(len(r["indicators"]) for r in rows),
        "nullBindings": nulls,
        "transformation": "Copy selected numeric values without aggregation, allocation or rounding.",
        "catalogueWritten": False,
        "mainRunsChanged": False,
    }
    if source_format == "parquet":
        receipt["sourceFormat"] = "parquet"
        receipt["sourceProjection"] = {
            "rows": (
                "object keyed by " + ", then ".join(key_columns)
                if key_columns
                else "list in file order"
            ),
            "keyColumns": key_columns or [],
            "pointerForm": (
                "/rows/" + "/".join(f"<{c}>" for c in key_columns) + "/<column>"
                if key_columns
                else "/rows/<index>/<column>"
            ),
        }
    # Only the process that creates this directory can clean it up on failure.
    out.mkdir(parents=True, exist_ok=False)
    try:
        path = out / "product.parquet"
        pd.DataFrame(rows).to_parquet(path, index=False)
        receipt["productSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        (out / "method.json").write_text(
            json.dumps(receipt, indent=2, allow_nan=False) + "\n"
        )
    except Exception:
        for path in (out / "product.parquet", out / "method.json"):
            path.unlink(missing_ok=True)
        out.rmdir()
        raise
    return receipt


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _region_records(
    geometries_run_id: str, offline_geometries_url: str | None
) -> list[dict]:
    """``{id, name, attributes}`` per geometry output of the run."""
    if offline_geometries_url is None:
        reader = CatalogueReader.from_environment()
        reader.read("geometries-run", geometries_run_id)
        records = reader.list_records("geometries-run", geometries_run_id, "outputs")
        regions = []
        for record in records:
            attributes = {}
            for field in ("metadata", "properties"):  # properties win
                if isinstance(record.get(field), dict):
                    attributes.update(record[field])
            regions.append(
                {
                    "id": record.get("id"),
                    "name": record.get("name"),
                    "attributes": attributes,
                }
            )
        return regions
    path, file_name = split_path_and_file_name_from_url(offline_geometries_url)
    raw = get_store_with_prefix_from_url(path or ".").get(file_name).bytes()
    try:
        table = pq.read_table(pa.BufferReader(bytes(raw)))
    except (pa.ArrowException, OSError):
        _fail("The offline geometries file must be a readable Parquet file.")
    if "csdr-id" not in table.column_names:
        _fail("The offline geometries parquet needs a csdr-id column.")
    columns = [name for name in table.column_names if name != "geometry"]
    return [
        {"id": row.get("csdr-id"), "name": row.get("csdr-name"), "attributes": row}
        for row in table.select(columns).to_pylist()
    ]


def bind_regions(
    geometries_run_id: str,
    *,
    key_column: str | None = None,
    key_source: str = "metadata",
    key_template: str | None = None,
    offline_geometries_url: str | None = None,
) -> dict:
    """A version 1 pre-computed specification skeleton with one binding per region.

    Each binding names the region's ``geometryOutputId`` and a keyed value pointer
    ``/rows/<key>/{{column}}``; every other decision is a ``{{...}}`` placeholder,
    which ``prepare-precomputed`` refuses until it is replaced.
    """
    _text(geometries_run_id, "geometries run ID", 200)
    if key_source not in KEY_SOURCES:
        _fail(f"The key source must be one of {list(KEY_SOURCES)}.")
    if key_source == "metadata":
        _text(key_column, "key column (--key-column)", 200)
    elif key_column is not None:
        _fail("--key-column is read only with --key-source metadata.")
    if key_template is not None and key_template.count("{key}") != 1:
        _fail("--key-template must contain {key} exactly once, e.g. 'region-{key}'.")
    keyed: dict[str, str] = {}
    problems: list[str] = []
    for region in _region_records(geometries_run_id, offline_geometries_url):
        output_id = region["id"]
        if not isinstance(output_id, str) or not output_id:
            _fail("Every geometry output needs an id.")
        if key_source == "name":
            value = region["name"]
        elif key_source == "csdr-id":
            value = output_id
        else:
            value = region["attributes"].get(key_column)
        if not (isinstance(value, str) and value.strip()) and type(value) is not int:
            problems.append(f"{output_id}: no usable key ({type(value).__name__})")
            continue
        key = str(value)
        if key_template is not None:
            key = key_template.replace("{key}", key)
        if key in keyed:
            problems.append(f"{output_id}: key {key!r} also used by {keyed[key]}")
            continue
        keyed[key] = output_id
    if problems:
        _fail(
            f"{len(problems)} geometry output(s) cannot be bound: {problems[:10]}. "
            "Choose a key that is present and unique per region."
        )
    if not keyed:
        _fail("The geometries run has no geometry outputs to bind.")
    binding = {
        "indicatorId": "{{indicatorId}}",
        "timePoint": "{{timePoint}}",
        "unit": "{{unit}}",
        "evidenceClass": "{{evidenceClass}}",
        "temporalBasis": "{{temporalBasis}}",
    }
    return {
        "version": 1,
        "productId": "{{productId}}",
        "productRunId": "{{productRunId}}",
        "datasetRunId": "{{datasetRunId}}",
        "geometriesRunId": geometries_run_id,
        "sourceSha256": "{{sourceSha256}}",
        "role": "{{role}}",
        "method": {
            "name": "{{method.name}}",
            "version": "{{method.version}}",
            "description": "{{method.description}}",
        },
        "reviewStatement": "{{reviewStatement}}",
        "caveats": ["{{caveat}}"],
        "sourceProvenancePointers": ["{{sourceProvenancePointer}}"],
        "bindings": [
            {
                "geometryOutputId": keyed[key],
                **binding,
                "valuePointer": f"/rows/{_escape_pointer_token(key)}/{COLUMN_PLACEHOLDER}",
            }
            for key in sorted(keyed)
        ],
    }
