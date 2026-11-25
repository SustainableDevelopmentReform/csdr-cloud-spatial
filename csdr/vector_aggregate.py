"""Vector aggregation with declared counting rules (``csdr products vector-aggregate``).

A version 1 JSON specification declares, per indicator, a measure (clipped area,
feature count, whole-feature attribute sum or distinct attribute count), the
features it selects (an optional class of the declared classification) and the
spatial relation between feature and boundary. Every geometry of a geometries run
is measured with SedonaDB SQL against the partitioned GeoParquet of a dataset run,
in the declared equal-area CRS, and written in the ``process-geometry`` output
format so ``consolidate``, ``parse_outputs`` and ``provenance product`` work
unchanged. See docs/vector-aggregate.md.
"""

import json
import logging
import math
import re
from datetime import UTC, date, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import sedona.db
import shapely
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from csdr.catalogue import CatalogueReader, digest
from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    read_dict,
    read_geospatial_file,
    split_path_and_file_name_from_url,
    write_json,
)
from csdr.parquet_source import parquet_source, sedona_options
from csdr.provenance import read_provenance
from csdr.utils import CSDRException

logger = logging.getLogger(__name__)

METHOD = "vector-aggregate"
METHOD_VERSION = "1"
SPEC_FIELDS = {
    "version",
    "productId",
    "productRunId",
    "datasetRunId",
    "geometriesRunId",
    "timePoint",
    "sourceDate",
    "areaCrs",
    "evidenceClass",
    "caveat",
    "countingRule",
    "classification",
    "measures",
}
OPTIONAL_SPEC_FIELDS = {"overlapPolicy"}
MEASURE_FIELDS = {"indicatorId", "measure"}
OPTIONAL_MEASURE_FIELDS = {"class", "attribute", "relation"}
# Equal-area CRSs in which predicates and areas are evaluated. This is the set the
# engine has been validated against (tests/test_vector_aggregate.py runs both);
# widen it only together with tests for the new CRS.
AREA_CRS = ("EPSG:3577", "EPSG:6933")
EVIDENCE_CLASSES = ("observed", "observed_register", "linked", "modelled")
OVERLAP_POLICIES = ("union", "sum-features")
AREA_MEASURES = ("area-m2", "area-ha")
ATTRIBUTE_MEASURES = ("sum-attribute", "distinct-attribute")
MEASURES = (*AREA_MEASURES, "count", *ATTRIBUTE_MEASURES)
RELATIONS = (
    "intersects",
    "within",
    "centroid-within",
    "representative-point-within",
    "covers-boundary",
)
RELATION_ALIASES = {"contains": "within"}
MAX_MEASURES = 1000
MULTI_INDICATOR_DIRECTORY = "many-indicators"
# Generous pre-filter margin in degrees around the boundary's EPSG:4326 box. It
# only selects candidates; every measure applies the exact predicate afterwards.
CANDIDATE_MARGIN_DEGREES = 0.01

MEASURE_DEFINITIONS = {
    "area-m2": "Clipped intersection area (m²) of the selected features with the boundary, in areaCrs. overlapPolicy union: overlapping features are unioned first, so shared area counts once; sum-features: each feature's clipped area is summed.",
    "area-ha": "As area-m2, divided by 10,000. Only for indicators whose catalogue unit is ha.",
    "count": "Number of selected features (rows) in the relation to the boundary.",
    "sum-attribute": "Sum of the attribute's whole-feature values over selected features in the relation; never apportioned by area. Null and NaN count as 0 and are recorded per geometry.",
    "distinct-attribute": "Number of distinct non-null values of the attribute over selected features in the relation. Nulls are recorded per geometry.",
}
RELATION_DEFINITIONS = {
    "intersects": "The feature and the boundary share at least one point (touching counts).",
    "within": "The boundary contains the feature: no part of the feature lies outside the boundary (ST_Within(feature, boundary)). Alias: contains.",
    "centroid-within": "The feature's centroid lies within the boundary (ST_Within(ST_Centroid(feature), boundary)).",
    "representative-point-within": "The feature's point on surface (a point guaranteed to lie in the feature, GEOS PointOnSurface) lies within the boundary.",
    "covers-boundary": "The feature covers the boundary: no part of the boundary lies outside the feature (ST_Covers(feature, boundary)).",
}


def _fail(message: str) -> None:
    raise CSDRException(message)


def _text(value: object, label: str, limit: int = 200) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(f"Declare a bounded, non-empty {label}.")


def _iso_date(value: object, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail(f"{label} must be an ISO date (YYYY-MM-DD).")
    try:
        date.fromisoformat(value)
    except ValueError:
        _fail(f"{label} must be an ISO date (YYYY-MM-DD).")


def _selection(value: object, label: str) -> dict:
    """``{column, values}``: one column and a non-empty list of unique values of one type."""
    if not isinstance(value, dict) or set(value) != {"column", "values"}:
        _fail(f"{label} must be an object with exactly 'column' and 'values'.")
    _text(value["column"], f"{label} column")
    values = value["values"]
    if not isinstance(values, list) or not values:
        _fail(f"{label} values must be a non-empty list.")
    strings = all(isinstance(item, str) for item in values)
    numbers = all(type(item) in {int, float} and math.isfinite(item) for item in values)
    if not (strings or numbers):
        _fail(f"{label} values must be all strings or all finite numbers.")
    if len(set(values)) != len(values):
        _fail(f"{label} values must not repeat.")
    return value


def validate_vector_aggregate_spec(spec: object) -> dict:
    """Validate a version 1 specification and return its effective plan.

    The plan holds the effective ``overlapPolicy`` and one normalised entry per
    measure (``relation`` resolved from aliases and defaults, the declared value
    kept as ``declaredRelation``).
    """
    if (
        not isinstance(spec, dict)
        or not SPEC_FIELDS <= set(spec) <= SPEC_FIELDS | OPTIONAL_SPEC_FIELDS
        or type(spec["version"]) is not int
        or spec["version"] != 1
    ):
        _fail(
            "Use the complete version 1 vector-aggregate specification without unknown fields."
        )
    for field in ("productId", "productRunId", "datasetRunId", "geometriesRunId"):
        _text(spec[field], f"SDF identifier {field}")
    _iso_date(spec["timePoint"], "timePoint")
    _iso_date(spec["sourceDate"], "sourceDate")
    if spec["areaCrs"] not in AREA_CRS:
        _fail(f"Select a documented equal-area CRS: {' or '.join(AREA_CRS)}.")
    if spec["evidenceClass"] not in EVIDENCE_CLASSES:
        _fail(f"evidenceClass must be one of {list(EVIDENCE_CLASSES)}.")
    _text(spec["caveat"], "measurement caveat", 5000)
    _text(spec["countingRule"], "counting rule", 5000)
    overlap_policy = spec.get("overlapPolicy", "union")
    if overlap_policy not in OVERLAP_POLICIES:
        _fail(f"overlapPolicy must be one of {list(OVERLAP_POLICIES)}.")
    classification = spec["classification"]
    if classification is not None:
        _selection(classification, "classification")
    measures = spec["measures"]
    if not isinstance(measures, list) or not 1 <= len(measures) <= MAX_MEASURES:
        _fail(f"Declare between 1 and {MAX_MEASURES} measures.")
    plan = []
    seen = set()
    for entry in measures:
        if (
            not isinstance(entry, dict)
            or not MEASURE_FIELDS
            <= set(entry)
            <= MEASURE_FIELDS | OPTIONAL_MEASURE_FIELDS
        ):
            _fail(
                "Each measure has indicatorId and measure, optionally class, attribute and relation, and no other fields."
            )
        indicator = entry["indicatorId"]
        if not isinstance(indicator, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", indicator
        ):
            _fail(
                "indicatorId must be 1-200 letters, digits, '.', '_' or '-' (it names output paths)."
            )
        if indicator in seen:
            _fail(f"indicatorId {indicator} is declared by more than one measure.")
        seen.add(indicator)
        measure = entry["measure"]
        if measure not in MEASURES:
            _fail(f"{indicator}: measure must be one of {list(MEASURES)}.")
        declared = entry.get("relation", "intersects")
        relation = RELATION_ALIASES.get(declared, declared)
        if relation not in RELATIONS:
            _fail(
                f"{indicator}: relation must be one of {list(RELATIONS)} (or the alias contains)."
            )
        if measure in AREA_MEASURES and relation != "intersects":
            _fail(
                f"{indicator}: area measures clip features to the boundary, so their relation is intersects."
            )
        attribute = entry.get("attribute")
        if measure in ATTRIBUTE_MEASURES:
            _text(attribute, f"{indicator} attribute")
        elif "attribute" in entry:
            _fail(f"{indicator}: attribute is only used by {list(ATTRIBUTE_MEASURES)}.")
        selection = entry.get("class")
        if selection is not None:
            _selection(selection, f"{indicator} class")
            if classification is not None and (
                selection["column"] != classification["column"]
                or any(
                    item not in classification["values"] for item in selection["values"]
                )
            ):
                _fail(
                    f"{indicator}: class values must belong to the declared classification column and values."
                )
        elif "class" in entry:
            _fail(
                f"{indicator}: class must be an object; omit it to select every feature."
            )
        plan.append(
            {
                "indicatorId": indicator,
                "measure": measure,
                "relation": relation,
                "declaredRelation": declared,
                "class": selection,
                "attribute": attribute,
            }
        )
    return {"overlapPolicy": overlap_policy, "measures": plan}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("Duplicate JSON keys make the specification ambiguous.")
        result[key] = value
    return result


def read_spec(url: str) -> dict:
    """Read a specification from a local path or store URL, strictly."""
    path, file_name = split_path_and_file_name_from_url(url)
    raw = get_store_with_prefix_from_url(path or ".").get(file_name).bytes()
    if len(raw) > 4 * 1024**2:
        _fail("The vector-aggregate specification exceeds 4 MiB.")
    try:
        return json.loads(
            bytes(raw),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _: _fail("Non-finite JSON numbers are not allowed."),
        )
    except (ValueError, UnicodeDecodeError, RecursionError):
        _fail("The vector-aggregate specification must be valid JSON.")


def _relation(record: dict, field: str) -> str | None:
    related = record.get(field)
    return related.get("id") if isinstance(related, dict) else None


def verify_catalogue(
    spec: dict,
    plan: dict,
    reader: CatalogueReader,
    dataset_provenance: dict,
    geometry_provenance: dict,
) -> dict:
    """Read-only identity and unit checks; snapshot digest compared before and after.

    The product, dataset run and geometries run must form one catalogue chain, the
    product run must be fresh, the dataset run must be the declared GeoParquet,
    and every indicator must exist as a measured indicator. ``area-ha`` requires
    unit ``ha`` and ``area-m2`` refuses it.
    """
    product = reader.read("product", spec["productId"])
    selected_organization = getattr(reader, "organization_id", None)
    if selected_organization and product.get("organizationId") != selected_organization:
        _fail("The product does not belong to the selected organisation.")
    dataset = reader.read("dataset-run", spec["datasetRunId"])
    geometries = reader.read("geometries-run", spec["geometriesRunId"])
    if reader.read("product-run", spec["productRunId"], missing_ok=True) is not None:
        _fail("Choose a fresh product run ID; existing runs must not be overwritten.")
    if (
        not _relation(product, "dataset")
        or _relation(product, "dataset") != _relation(dataset, "dataset")
        or not _relation(product, "geometries")
        or _relation(product, "geometries") != _relation(geometries, "geometries")
    ):
        _fail("The product and input runs do not form the same catalogue chain.")
    if dataset.get("dataType") != "geoparquet" or dataset.get(
        "dataUrl"
    ) != dataset_provenance.get("dataUrl"):
        _fail(
            "The dataset run must be the GeoParquet named by the dataset provenance file (dataType and dataUrl)."
        )
    if geometries.get("dataUrl") not in (None, geometry_provenance.get("dataUrl")):
        _fail("The geometries run's dataUrl differs from the geometry provenance file.")
    indicators = {}
    for entry in plan["measures"]:
        record = reader.read("indicator/measured", entry["indicatorId"])
        unit = record.get("unit")
        if entry["measure"] == "area-ha" and unit != "ha":
            _fail(
                f"{entry['indicatorId']}: area-ha needs a measured indicator with unit ha (catalogue unit: {unit!r})."
            )
        if entry["measure"] == "area-m2" and unit == "ha":
            _fail(
                f"{entry['indicatorId']}: the catalogue unit is ha; declare area-ha, not area-m2."
            )
        indicators[entry["indicatorId"]] = {
            key: record.get(key) for key in ("id", "updatedAt", "name", "unit")
        }
    snapshot = {
        "product": {
            key: product.get(key) for key in ("id", "updatedAt", "organizationId")
        },
        "datasetRun": {
            key: dataset.get(key)
            for key in ("id", "updatedAt", "dataUrl", "dataType", "provenanceJson")
        },
        "geometriesRun": {
            key: geometries.get(key)
            for key in ("id", "updatedAt", "dataUrl", "provenanceJson")
        },
        "indicators": indicators,
        "datasetId": _relation(product, "dataset"),
        "geometriesId": _relation(product, "geometries"),
    }
    return {
        "status": "catalogue-inputs-matched",
        "apiOrigin": reader.hostname,
        "checkedAt": datetime.now(UTC).isoformat(),
        "snapshotSha256": digest(snapshot),
        "indicatorUnits": {key: value["unit"] for key, value in indicators.items()},
        "limitation": "Read-only catalogue snapshot, not scientific approval or an atomic publication lock. Geometry output membership is checked when outputs are posted.",
    }


# --- SQL helpers ---------------------------------------------------------------


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(value: str | float) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(value)


def _column_kind(data_type: pa.DataType) -> str:
    if pa.types.is_dictionary(data_type):
        data_type = data_type.value_type
    if (
        pa.types.is_string(data_type)
        or pa.types.is_large_string(data_type)
        or (hasattr(pa.types, "is_string_view") and pa.types.is_string_view(data_type))
    ):
        return "string"
    if pa.types.is_integer(data_type):
        return "integer"
    if pa.types.is_floating(data_type):
        return "float"
    return "other"


class _Columns:
    """Source columns a plan needs, aliased so they cannot clash with engine columns."""

    def __init__(self, schema: pa.Schema, spec: dict, plan: dict) -> None:
        kinds = {field.name: _column_kind(field.type) for field in schema}
        if "geometry" not in kinds:
            _fail("The dataset partitions need a 'geometry' column.")
        needed = []
        selections = [spec["classification"]] + [m["class"] for m in plan["measures"]]
        for selection in selections:
            if selection is None:
                continue
            column = selection["column"]
            if column not in kinds:
                _fail(f"Class column {column!r} is not in the dataset.")
            numeric = all(not isinstance(v, str) for v in selection["values"])
            if (kinds[column] == "string") == numeric or kinds[column] == "other":
                _fail(
                    f"Class values for {column!r} must match the column type ({kinds[column]})."
                )
            needed.append(column)
        for entry in plan["measures"]:
            column = entry["attribute"]
            if column is None:
                continue
            if column not in kinds:
                _fail(f"Attribute column {column!r} is not in the dataset.")
            if entry["measure"] == "sum-attribute" and kinds[column] not in {
                "integer",
                "float",
            }:
                _fail(
                    f"sum-attribute needs a numeric column; {column!r} is {kinds[column]}."
                )
            needed.append(column)
        self.kinds = kinds
        # Reserved prefix, so no dataset column name can collide with an engine alias.
        self.alias = {
            name: f"{ALIAS_PREFIX}c{i}" for i, name in enumerate(dict.fromkeys(needed))
        }

    def select_list(self) -> str:
        return "".join(
            f"{_identifier(name)} AS {alias}, " for name, alias in self.alias.items()
        )

    def class_sql(self, selection: dict | None) -> str:
        if selection is None:
            return "TRUE"
        values = ", ".join(_literal(value) for value in selection["values"])
        return f"c.{self.alias[selection['column']]} IN ({values})"


# Engine column names in SQL. The reserved prefix keeps them distinct from dataset
# columns (a source column named "p" or "g" must not shadow the geometry).
ALIAS_PREFIX = "__csdr_"
_GEOMETRY = f"{ALIAS_PREFIX}g"
_PROJECTED = f"{ALIAS_PREFIX}projected"
_VALID = f"{ALIAS_PREFIX}valid"
_WKB = f"{ALIAS_PREFIX}wkb"
_RELATION_SQL = {
    "intersects": f"ST_Intersects(c.{_GEOMETRY}, b.geometry)",
    "within": f"ST_Within(c.{_GEOMETRY}, b.geometry)",
    "centroid-within": f"ST_Within(ST_Centroid(c.{_GEOMETRY}), b.geometry)",
    "covers-boundary": f"ST_Covers(c.{_GEOMETRY}, b.geometry)",
}
_INTERSECTS = _RELATION_SQL["intersects"]


def _polygonal_parts(geometries: object) -> object:
    """Polygon parts of each geometry, flattening multi-parts and collections.

    A clip can yield lines or points where a feature touches the boundary, and a
    GeometryCollection where a multi-part feature both overlaps and touches it;
    those have no area and are dropped before union or summation.
    """
    parts = shapely.get_parts(geometries)
    while True:
        # Multi-parts (4-6) and GeometryCollections (7) nested in a collection.
        nested = shapely.get_type_id(parts) >= 4
        if not nested.any():
            break
        parts = np.concatenate([parts[~nested], shapely.get_parts(parts[nested])])
    keep = (shapely.get_type_id(parts) == 3) & ~shapely.is_empty(parts)  # Polygon
    return parts[keep]


def _candidate_window(boundary: BaseGeometry, area_crs: str) -> str:
    """EPSG:4326 WKT box around the boundary, with a margin, for the pre-filter."""
    envelope = shapely.segmentize(box(*boundary.bounds), max_segment_length=1000.0)
    minx, miny, maxx, maxy = (
        gpd.GeoSeries([envelope], crs=area_crs).to_crs(4326).total_bounds
    )
    m = CANDIDATE_MARGIN_DEGREES
    window = box(
        max(minx - m, -180), max(miny - m, -90), min(maxx + m, 180), min(maxy + m, 90)
    )
    return shapely.to_wkt(window, rounding_precision=-1)


def aggregate_boundary(
    sd: sedona.db.context.SedonaContext,
    dataset_view: str,
    boundary: BaseGeometry,
    spec: dict,
    plan: dict,
    columns: _Columns,
) -> tuple[dict, dict]:
    """Measure every declared indicator for one boundary given in ``areaCrs``.

    Returns ``(values, details)``. Values are plain Python numbers; a known source
    with no qualifying features gives 0.
    """
    crs = spec["areaCrs"]
    window = _candidate_window(boundary, crs)
    # 1. Candidates: bounding-box pre-filter in EPSG:4326 (partition files and row
    # groups are pruned from GeoParquet statistics), reprojected to areaCrs and
    # repaired there, then held in memory for the per-measure queries.
    sd.sql(
        f"""
        SELECT {columns.select_list()}ST_MakeValid(t.{_PROJECTED}) AS {_GEOMETRY},
               ST_IsValid(t.{_PROJECTED}) AS {_VALID}
        FROM (
            SELECT *, ST_Transform(geometry, '{crs}') AS {_PROJECTED}
            FROM {dataset_view}
            WHERE geometry IS NOT NULL
              AND ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{window}'), 4326))
        ) t
        """
    ).to_memtable().to_view("vector_aggregate_candidates", overwrite=True)
    sd.create_data_frame(gpd.GeoDataFrame(geometry=[boundary], crs=crs)).to_view(
        "vector_aggregate_boundary", overwrite=True
    )
    source = "vector_aggregate_candidates c CROSS JOIN vector_aggregate_boundary b"

    classification = spec["classification"]
    outside = (
        f", COALESCE(SUM(CASE WHEN {_INTERSECTS} AND NOT COALESCE({columns.class_sql(classification)}, FALSE) THEN 1 ELSE 0 END), 0) AS outside"
        if classification is not None
        else ""
    )
    summary = (
        sd.sql(
            f"""
            SELECT COUNT(*) AS candidates,
                   COALESCE(SUM(CASE WHEN c.{_VALID} THEN 0 ELSE 1 END), 0) AS repaired,
                   COALESCE(SUM(CASE WHEN {_INTERSECTS} THEN 1 ELSE 0 END), 0) AS intersecting
                   {outside}
            FROM {source}
            """
        )
        .to_pandas()
        .iloc[0]
    )

    # 2. Relation flags and attributes for the non-area measures, in one query.
    relations = sorted(
        {m["relation"] for m in plan["measures"] if m["measure"] not in AREA_MEASURES}
    )
    flags = None
    if relations:
        expressions = [
            f"{_RELATION_SQL[r]} AS {_flag(r)}" for r in relations if r in _RELATION_SQL
        ]
        if "representative-point-within" in relations:
            expressions.append(f"ST_AsBinary(c.{_GEOMETRY}) AS {_WKB}")
        selected = "".join(f"c.{alias}, " for alias in columns.alias.values())
        flags = sd.sql(
            f"SELECT {selected}{', '.join(expressions)} FROM {source}"
        ).to_pandas()
        if "representative-point-within" in relations:
            # SedonaDB 0.2 has no ST_PointOnSurface; shapely's point_on_surface is
            # the same GEOS PointOnSurface operation.
            points = shapely.point_on_surface(shapely.from_wkb(flags[_WKB].to_numpy()))
            flags[_flag("representative-point-within")] = shapely.within(
                points, boundary
            )

    values = {}
    details = {}
    for entry in plan["measures"]:
        indicator, measure = entry["indicatorId"], entry["measure"]
        null_count = None
        if measure in AREA_MEASURES:
            # SedonaDB clips; the union or sum runs in shapely over polygon parts
            # only (SedonaDB 0.2 has no ST_CollectionExtract, and ST_Union_Agg
            # fails on lines and drops GeometryCollections).
            clips = sd.sql(
                f"""
                SELECT ST_AsBinary(ST_Intersection(c.{_GEOMETRY}, b.geometry)) AS {_WKB}
                FROM {source}
                WHERE {columns.class_sql(entry["class"])} AND {_INTERSECTS}
                """
            ).to_pandas()
            parts = _polygonal_parts(shapely.from_wkb(clips[_WKB].to_numpy()))
            if len(parts) == 0:
                area_m2 = 0.0
            elif plan["overlapPolicy"] == "union":
                area_m2 = float(shapely.union_all(parts).area)
            else:
                # Parts of one valid feature do not overlap, so this is the sum of
                # each feature's clipped area.
                area_m2 = float(shapely.area(parts).sum())
            value = area_m2 / 10_000 if measure == "area-ha" else area_m2
            feature_count = len(clips)
        else:
            mask = flags[_flag(entry["relation"])].astype(bool)
            if entry["class"] is not None:
                alias = columns.alias[entry["class"]["column"]]
                mask &= flags[alias].isin(entry["class"]["values"])
            feature_count = int(mask.sum())
            if measure == "count":
                value = feature_count
            else:
                selected_values = flags.loc[mask, columns.alias[entry["attribute"]]]
                null_count = int(pd.isna(selected_values).sum())
                if measure == "sum-attribute":
                    total = pd.to_numeric(selected_values).fillna(0).sum()
                    value = (
                        int(total)
                        if columns.kinds[entry["attribute"]] == "integer"
                        else float(total)
                    )
                else:
                    value = int(selected_values.dropna().nunique())
        values[indicator] = value
        details[indicator] = {
            "measure": measure,
            "relation": entry["relation"],
            "declaredRelation": entry["declaredRelation"],
            "class": entry["class"],
            "attribute": entry["attribute"],
            "overlapPolicy": plan["overlapPolicy"]
            if measure in AREA_MEASURES
            else None,
            "featureCount": feature_count,
            "nullAttributeCount": null_count,
        }
    counts = {
        "candidateFeatureCount": int(summary["candidates"]),
        "intersectingFeatureCount": int(summary["intersecting"]),
        "repairedFeatureCount": int(summary["repaired"]),
        "outsideClassificationFeatureCount": (
            int(summary["outside"]) if classification is not None else None
        ),
    }
    return values, {"counts": counts, "measures": details}


def _flag(relation: str) -> str:
    return f"{ALIAS_PREFIX}r_" + relation.replace("-", "_")


# --- Boundaries ------------------------------------------------------------------


def _boundary_in_area_crs(
    geometry: BaseGeometry, crs: object, area_crs: str
) -> tuple[BaseGeometry, bool]:
    """Reproject one geometry-set boundary to ``areaCrs``; repair it there if needed."""
    if geometry is None or geometry.is_empty:
        _fail("A boundary geometry is missing or empty.")
    projected = gpd.GeoSeries([geometry], crs=crs).to_crs(area_crs).iloc[0]
    repaired = not projected.is_valid
    if repaired:
        projected = shapely.make_valid(projected)
        if projected.geom_type == "GeometryCollection":
            projected = shapely.union_all(
                [
                    part
                    for part in projected.geoms
                    if part.geom_type in {"Polygon", "MultiPolygon"}
                ]
            )
    if projected.is_empty or projected.geom_type not in {"Polygon", "MultiPolygon"}:
        _fail("Boundaries must be polygons or multipolygons.")
    return projected, repaired


def load_boundaries(
    geometries_url: str, geometry_ids: list[str] | None
) -> tuple[gpd.GeoDataFrame, list[str]]:
    """Read the geometries parquet; return it indexed by csdr-id and the ids to process."""
    frame = read_geospatial_file(geometries_url)
    if (
        not isinstance(frame, gpd.GeoDataFrame)
        or frame.crs is None
        or "csdr-id" not in frame.columns
    ):
        _fail("The geometries parquet needs a CRS, geometries and a csdr-id column.")
    ids = frame["csdr-id"].tolist()
    duplicates = sorted(set(frame.loc[frame["csdr-id"].duplicated(), "csdr-id"]))
    if duplicates:
        _fail(f"The geometries parquet repeats csdr-id values: {duplicates[:10]}")
    if geometry_ids is None:
        geometry_ids = [str(one) for one in ids]
    missing = sorted(set(geometry_ids) - set(map(str, ids)))
    if missing:
        _fail(f"Geometry ids not found in '{geometries_url}': {missing[:10]}")
    return frame.set_index(frame["csdr-id"].astype(str)), geometry_ids


# --- Run -------------------------------------------------------------------------


def output_directory(plan: dict) -> str:
    """The ``consolidate --indicator-name`` value for this specification."""
    measures = plan["measures"]
    return (
        measures[0]["indicatorId"] if len(measures) == 1 else MULTI_INDICATOR_DIRECTORY
    )


def output_path(spec: dict, plan: dict, geometry_id: str) -> str:
    return f"{output_directory(plan)}/{spec['timePoint']}/{spec['productId']}-{geometry_id}.json"


def method_path(spec: dict, plan: dict) -> str:
    # Beside the per-geometry files; consolidate skips *.method.json.
    return f"{output_directory(plan)}/{spec['productId']}.method.json"


def method_document(
    spec: dict,
    plan: dict,
    *,
    dataset_provenance_url: str,
    geometry_provenance_url: str,
    dataset_provenance: dict,
    geometry_provenance: dict,
    partition_source: str,
    offline: bool,
) -> dict:
    """The run-level method record. Deterministic, so every fan-out pod writes the same bytes."""
    uses_ha = any(m["measure"] == "area-ha" for m in plan["measures"])
    return {
        "method": METHOD,
        "methodVersion": METHOD_VERSION,
        "specification": spec,
        "specificationSha256": digest(spec),
        "overlapPolicy": plan["overlapPolicy"],
        "measures": plan["measures"],
        "measureDefinitions": {m: MEASURE_DEFINITIONS[m] for m in MEASURES},
        "relationDefinitions": RELATION_DEFINITIONS,
        "areaCrs": spec["areaCrs"],
        "countingRule": spec["countingRule"],
        "candidateFilter": f"EPSG:4326 bounding box of the boundary plus {CANDIDATE_MARGIN_DEGREES} degrees; exact predicates are evaluated in areaCrs.",
        "geometryRepair": "Features and boundaries are reprojected to areaCrs and invalid ones repaired there with make_valid; repairs are counted per geometry.",
        "zeroPolicy": "A known source with no qualifying features gives 0 for that geometry.",
        "featureIdentity": "One feature is one row of the dataset partitions.",
        "datasetProvenanceUrl": dataset_provenance_url,
        "geometryProvenanceUrl": geometry_provenance_url,
        "datasetDataUrl": dataset_provenance.get("dataUrl"),
        "datasetDataEtag": dataset_provenance.get("dataEtag"),
        "datasetSourceSha256": dataset_provenance.get("sourceSha256"),
        "partitionSource": partition_source,
        "geometriesDataUrl": geometry_provenance.get("dataUrl"),
        "catalogueVerification": "offline: not verified"
        if offline
        else "catalogue-inputs-matched before and after calculation (per-geometry outputs carry the snapshot digest)",
        "areaHaUnitCheck": (
            None
            if not uses_ha
            else "unchecked (offline)"
            if offline
            else "catalogue unit ha"
        ),
    }


def _partition_source(data_url: str) -> str:
    path, _ = split_path_and_file_name_from_url(data_url)
    prefix = f"{path}/partition/"
    if "://" not in prefix and not Path(prefix).is_dir():
        _fail(
            "The dataset needs the partitioned GeoParquet layout <dir>/partition/*.parquet "
            "(csdr convert vector-to-geoparquet --partition)."
        )
    return prefix


def run_vector_aggregate(
    *,
    spec_url: str,
    run_id: str,
    target_location: str,
    dataset_provenance_url: str,
    geometry_provenance_url: str,
    geometry_ids: list[str] | None,
    offline: bool = False,
    overwrite: bool = False,
) -> dict:
    """Measure the specification for the given geometry ids (``None`` = every geometry)."""
    from csdr.cli_products import _create_product_output

    spec = read_spec(spec_url)
    plan = validate_vector_aggregate_spec(spec)
    if run_id != spec["productRunId"]:
        _fail("--run-id must equal the specification's productRunId.")
    target_location = target_location.rstrip("/")
    store = get_store_with_prefix_from_url(target_location)

    frame = None
    geometry_provenance = None
    if geometry_ids is None:
        geometry_provenance = read_provenance(geometry_provenance_url)
        frame, geometry_ids = load_boundaries(geometry_provenance.get("dataUrl"), None)
    pending = [
        one
        for one in geometry_ids
        if overwrite or not exists(store, output_path(spec, plan, one))
    ]
    skipped = len(geometry_ids) - len(pending)
    if not pending:
        logger.info("Every requested geometry already has an output; nothing to do.")
        return {"written": 0, "skipped": skipped}

    if geometry_provenance is None:
        geometry_provenance = read_provenance(geometry_provenance_url)
    dataset_provenance = read_provenance(dataset_provenance_url)
    if dataset_provenance.get("dataType") != "geoparquet" or not dataset_provenance.get(
        "dataUrl"
    ):
        _fail("The dataset provenance must declare a geoparquet dataUrl.")
    declared_run = geometry_provenance.get("geometriesRunId")
    if declared_run is not None and declared_run != spec["geometriesRunId"]:
        _fail(
            "The geometry provenance belongs to a different geometries run than the specification."
        )
    partition_source = _partition_source(dataset_provenance["dataUrl"])
    method = method_document(
        spec,
        plan,
        dataset_provenance_url=dataset_provenance_url,
        geometry_provenance_url=geometry_provenance_url,
        dataset_provenance=dataset_provenance,
        geometry_provenance=geometry_provenance,
        partition_source=partition_source,
        offline=offline,
    )
    method_file = method_path(spec, plan)
    if (
        exists(store, method_file)
        and not overwrite
        and read_dict(store, method_file) != method
    ):
        _fail(
            "This target already holds outputs of a different specification or input set "
            f"({method_file}). Use a new target location, or --overwrite to replace them."
        )

    reader = None if offline else CatalogueReader.from_environment()
    before = (
        verify_catalogue(spec, plan, reader, dataset_provenance, geometry_provenance)
        if reader is not None
        else None
    )
    if frame is None:
        frame, _ = load_boundaries(geometry_provenance.get("dataUrl"), pending)

    sd = sedona.db.connect()
    results = []
    with parquet_source(partition_source) as readable:
        sd.read_parquet(readable, options=sedona_options(readable)).to_view(
            "vector_aggregate_dataset", overwrite=True
        )
        columns = _Columns(
            sd.sql("SELECT * FROM vector_aggregate_dataset LIMIT 0")
            .to_arrow_table()
            .schema,
            spec,
            plan,
        )
        for geometry_id in pending:
            boundary, boundary_repaired = _boundary_in_area_crs(
                frame.geometry.loc[geometry_id], frame.crs, spec["areaCrs"]
            )
            logger.info(
                f"Aggregating {len(plan['measures'])} measure(s) for geometry '{geometry_id}'"
            )
            values, details = aggregate_boundary(
                sd, "vector_aggregate_dataset", boundary, spec, plan, columns
            )
            results.append(
                (geometry_id, values, details, boundary_repaired, float(boundary.area))
            )

    check = {"status": "not-verified"}
    if reader is not None:
        after = verify_catalogue(
            spec, plan, reader, dataset_provenance, geometry_provenance
        )
        if after["snapshotSha256"] != before["snapshotSha256"]:
            _fail(
                "Catalogue inputs changed during calculation. No output was written; start a fresh run."
            )
        check = {
            key: after[key]
            for key in ("status", "apiOrigin", "snapshotSha256", "indicatorUnits")
        }
        check["checkedBeforeCalculationAt"] = before["checkedAt"]
        check["checkedAfterCalculationAt"] = after["checkedAt"]

    specification_hash = digest(spec)
    for geometry_id, values, details, boundary_repaired, boundary_area in results:
        output = _create_product_output(
            spec["productId"],
            geometry_id,
            geometry_provenance_url,
            dataset_provenance_url,
            spec["timePoint"],
            values,
            run_id,
        )
        output["metadata"]["vectorAggregate"] = {
            "method": METHOD,
            "methodVersion": METHOD_VERSION,
            "specificationSha256": specification_hash,
            "countingRule": spec["countingRule"],
            "overlapPolicy": plan["overlapPolicy"],
            "areaCrs": spec["areaCrs"],
            "evidenceClass": spec["evidenceClass"],
            "caveat": spec["caveat"],
            "sourceDate": spec["sourceDate"],
            "classification": spec["classification"],
            "datasetRunId": spec["datasetRunId"],
            "geometriesRunId": spec["geometriesRunId"],
            "boundaryAreaM2": boundary_area,
            "boundaryRepaired": boundary_repaired,
            **details["counts"],
            "measures": details["measures"],
            "catalogueVerification": check,
        }
        write_json(store, output_path(spec, plan, geometry_id), output)
    write_json(store, method_file, method)
    logger.info(
        f"Wrote {len(results)} output(s) to {target_location}/{output_directory(plan)}"
    )
    return {"written": len(results), "skipped": skipped}
