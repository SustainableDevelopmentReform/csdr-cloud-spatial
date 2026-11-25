# Vector aggregation with declared counting rules

`csdr products vector-aggregate` measures a partitioned vector dataset over every boundary of a
geometries run. A version 1 JSON specification declares each indicator's counting rule: the measure
(clipped area, feature count, whole-feature attribute sum or distinct attribute values), which
features it selects (a class of the declared classification) and the spatial relation between
feature and boundary. Outputs use the `process-geometry` format, so `consolidate`, `parse_outputs`
and `provenance product` work unchanged.

```sh
csdr products vector-aggregate \
  --spec spec.json --run-id <productRunId> \
  --target-location ./cache/products/<product>/0-0-1/runs/<productRunId> \
  --dataset-provenance-url <dataset dir>/<name>.parquet.provenance.json \
  --geometry-provenance-url <geometries run>/<name>.parquet.provenance.json \
  (--geometry-id ID | --geometry-ids a,b,c | --all-geometries) [--offline] [--overwrite]
```

| Option | Meaning |
|---|---|
| `--spec` | Specification JSON, local path or store URL. Duplicate keys and non-finite numbers are refused |
| `--run-id` | Must equal the specification's `productRunId` |
| `--target-location` | One JSON per geometry at `<dir>/<timePoint>/<productId>-<geometry id>.json`, where `<dir>` is the indicator id for a single measure and `many-indicators` otherwise. The run's `<dir>/<productId>.method.json` sits beside them |
| `--dataset-provenance-url` | Provenance file of `datasetRunId`. It needs `dataType: geoparquet` and a `dataUrl` with the `<dir>/partition/*.parquet` layout (`csdr convert vector-to-geoparquet --partition`; partitions are EPSG:4326) |
| `--geometry-provenance-url` | Provenance file of `geometriesRunId`. Its `dataUrl` is the geometries parquet (`csdr-id`, any CRS). If it records `geometriesRunId`, that must match the specification |
| `--geometry-id`, `--geometry-ids`, `--all-geometries` | Give exactly one. A batch runs in one process and writes one file per geometry. `--all-geometries` iterates the geometries parquet (for local runs) |
| `--offline` | Skip catalogue reads. Outputs are marked `not-verified`, and `area-ha` unit checks are recorded as unchecked |
| `--overwrite/--no-overwrite` | Default `--no-overwrite`: finished geometries are skipped before any input is read, so a retried batch resumes. A target that already holds a different specification or input set (its `.method.json` differs) is refused unless `--overwrite` is given |

The two provenance URLs are required even though the specification names the runs.
`consolidate` compares every output's `metadata.datasetProvenanceUrl` and
`metadata.geometryProvenanceUrl` with its own required options. An `--offline` run has no catalogue
in which to resolve run ids to files. Online, the catalogue run records are cross-checked against
these files. The option names match `process-geometry`, so the Argo engine template has the same
shape.

The fan-out command writes no provenance step. `consolidate --indicator-name <dir>` writes the
summary step on its behalf (`Aggregate vector features for N geometries ...`, source
`vector_aggregate_command`, `--geometry-ids` templated, `specification_sha256` in the inputs), then
the consolidate step. `consolidate` skips `*.method.json`.

## Specification (version 1)

The field set is exact: unknown fields are refused. `overlapPolicy` is the only optional top-level
field.

| Field | Value |
|---|---|
| `version` | `1` |
| `productId`, `productRunId`, `datasetRunId`, `geometriesRunId` | Existing SDF identifiers |
| `timePoint` | ISO date. Output `timePoint` is `<date>T00:00:00Z` |
| `sourceDate` | ISO date of the source snapshot, recorded |
| `areaCrs` | `EPSG:3577` or `EPSG:6933` (`AREA_CRS` in `csdr/vector_aggregate.py`: the equal-area CRSs the engine has been validated against). All predicates and areas are computed in this CRS |
| `evidenceClass` | `observed`, `observed_register`, `linked` or `modelled` |
| `caveat`, `countingRule` | Non-empty text, copied into every output and the method record |
| `overlapPolicy` | `union` (default) or `sum-features` |
| `classification` | `{column, values}` (the declared class domain) or `null` |
| `measures` | 1 to 1000 entries `{indicatorId, measure, class?, attribute?, relation?}` |

Measure entries:

| Field | Value |
|---|---|
| `indicatorId` | Any id the specification declares (letters, digits, `.`, `_`, `-`; it names output paths). Unique per specification. Catalogue-verified unless `--offline`. No indicator-registry family is needed |
| `measure` | `area-m2`, `area-ha`, `count`, `sum-attribute` or `distinct-attribute` |
| `class` | `{column, values}`. Values are all strings or all numbers, unique, and match the column type. With a declared `classification`, the column must be the classification column and the values must be among its values. Omit it to select every feature |
| `attribute` | Required for `sum-attribute` (numeric column) and `distinct-attribute`; refused otherwise |
| `relation` | Default `intersects`. Area measures take `intersects` only, because the clip is their relation |

## Semantics

| Measure | Value for one boundary |
|---|---|
| `area-m2` | Clipped intersection area of the class's features with the boundary, in m² in `areaCrs`. `union`: overlapping features are unioned first, so shared area counts once (two 10,000 m² squares overlapping by half give 15,000 m²). `sum-features`: each feature's clipped area is summed (20,000 m²) |
| `area-ha` | `area-m2` / 10,000. Allowed only when the catalogue unit of the indicator is `ha`. Offline it is allowed and recorded as unchecked. `area-m2` is refused when the catalogue unit is `ha` |
| `count` | Number of selected features (rows) in the relation |
| `sum-attribute` | Sum of the attribute's whole-feature values over selected features in the relation. A feature partly inside counts with its full value: values are never apportioned. Null and NaN count as 0, and their number is recorded (`nullAttributeCount`). Integer columns give integers |
| `distinct-attribute` | Number of distinct non-null attribute values over selected features in the relation. Nulls are recorded |

A known source with no qualifying features gives `0`. This is a zero, not an absence. Classes are
measured separately: there is no cross-class total unless a measure declares one, by listing
several values in its `class`.

## Relations

| Relation | Feature counted when |
|---|---|
| `intersects` | Feature and boundary share at least one point. A feature that only touches the boundary counts (area 0) |
| `within` (alias `contains`) | The boundary contains the feature: no part of the feature is outside (`ST_Within(feature, boundary)`) |
| `centroid-within` | The feature's centroid lies within the boundary. This can hold for a feature that does not touch the boundary (a ring around it) |
| `representative-point-within` | The feature's point on surface lies within the boundary. This is a point guaranteed to lie in the feature (GEOS PointOnSurface, as geopandas `representative_point`) |
| `covers-boundary` | The feature covers the boundary: no part of the boundary is outside the feature (`ST_Covers(feature, boundary)`) |

The output records `relation` (effective) and `declaredRelation` (as written, so `contains` stays
visible).

Relations that depend on shared edges (`within`, `covers-boundary`, touching) depend on vertex
density and on `areaCrs`. An edge is straight between its vertices in the CRS where the predicate
is evaluated (`areaCrs`), not in longitude and latitude. A long edge along a parallel in EPSG:4326
is curved in EPSG:3577, so a feature vertex that lies on the boundary's EPSG:4326 edge can be
metres off the boundary's projected chord. Example: `box(148, -34, 148.5, -33.5)` inside
`box(148, -34, 149, -33)` is `within` in EPSG:6933 but not in EPSG:3577. The reference pipeline
reprojects both layers the same way, so a spec reproduces it when it declares the same `areaCrs`.
Densify boundaries before conversion if shared edges must hold in the projected CRS.

## Execution

For each boundary:

1. **Candidate pre-filter.** The boundary is reprojected to `areaCrs` and repaired there if
   invalid. Candidates are the features whose EPSG:4326 geometry intersects the boundary's box plus
   0.01°. SedonaDB prunes partition files and row groups from GeoParquet statistics. The source
   is opened with `parquet_source` (`csdr/parquet_source.py`): a local folder is read in place, and
   an `s3://…/partition/` prefix is read in place, unsigned, with the region from `AWS_REGION` or
   `AWS_DEFAULT_REGION`. A private bucket therefore needs a local copy of the partitions.
2. **Reproject and repair.** Candidates are reprojected to `areaCrs` with `ST_Transform` and repaired
   with `ST_MakeValid`. Repairs are counted (`repairedFeatureCount`). The candidates are then held
   in memory. Engine columns use the reserved prefix `__csdr_`, so a source column named, for
   example, `p` or `g` cannot shadow them.
3. **Area measures.** For each area measure, SedonaDB clips the class's intersecting features with
   `ST_Intersection`. The union or sum then runs in shapely over polygon parts only. Multi-parts and
   GeometryCollections are flattened, and the line or point parts that touching features produce
   are dropped:
   - `union`: `union_all(parts).area`;
   - `sum-features`: the sum of part areas, which equals the sum of each feature's clipped area,
     because the parts of one valid feature do not overlap.

   SedonaDB 0.2 has no `ST_CollectionExtract`, and its `ST_Union_Agg` fails on lines and drops
   GeometryCollections. `featureCount` counts every intersecting feature, touching ones included.
4. **Count and attribute measures.** One query computes the relation flags with `ST_Intersects`,
   `ST_Within`, `ST_Within(ST_Centroid(...))` and `ST_Covers`. SedonaDB 0.2 has no
   `ST_PointOnSurface`, so `representative-point-within` uses shapely's `point_on_surface`, which
   is the same GEOS operation.

Per-geometry `metadata` keeps `geometryProvenanceUrl`, `datasetProvenanceUrl` and `command` (as
`process-geometry` does) and adds `vectorAggregate`:

- the method, its version and `specificationSha256`;
- `countingRule`, `overlapPolicy`, `areaCrs`, `evidenceClass`, `caveat`, `sourceDate`,
  `classification` and the run ids;
- `boundaryAreaM2` and `boundaryRepaired`;
- the feature counts `candidateFeatureCount`, `intersectingFeatureCount`, `repairedFeatureCount`
  and `outsideClassificationFeatureCount` (intersecting features whose class is outside the
  declared domain, which catches spelling mismatches);
- `measures.<indicatorId>`: `{measure, relation, declaredRelation, class, attribute, overlapPolicy,
  featureCount, nullAttributeCount}`;
- `catalogueVerification`.

The `.method.json` holds the run-level record. It is deterministic, so every fan-out pod writes
the same bytes. It contains the specification and its digest, the effective measures, the measure
and relation definitions, the candidate, repair and zero policies, the provenance URLs, the dataset
`dataUrl` and etag, the partition source, and the verification mode.

## Catalogue verification

Unless `--offline` is given, `CSDR_API_HOSTNAME`, `CSDR_API_KEY` and, optionally,
`CSDR_API_ORGANIZATION_ID` select a read-only catalogue (`csdr/catalogue.py`; reads follow the same
transport rules as posts: organisation header and membership check, timeouts, no redirects, and a
2 MiB response cap). The checks run before the calculation and again after it; if the snapshot
digest differs, nothing is written:

- the product (and its organisation), the dataset run and the geometries run form one chain;
- the product run is fresh;
- the dataset run is `geoparquet` with the provenance file's `dataUrl`;
- the geometries run's `dataUrl`, when it has one, matches the provenance file;
- every `indicatorId` exists as a measured indicator, and its unit passes the `area-ha` and
  `area-m2` rules.

Geometry-output membership is checked when `provenance product --post-to-database` posts the rows.
Outputs carry the snapshot digest, the indicator units and both check times.

## Example

`examples/vector-aggregate/example-spec.json` runs on the vector fixture (generic placeholder ids,
so `--offline`):

```sh
make convert-vector-fixture-local convert-vector-fixture-provenance-local \
     geometry-vector-fixture-convert-local geometry-vector-fixture-provenance-local \
     product-vector-fixture-vector-aggregate-local product-vector-fixture-vector-aggregate-consolidate-local
```

```json
{
  "version": 1,
  "productId": "example-vector-product",
  "productRunId": "example-vector-product-run",
  "datasetRunId": "example-dataset-run",
  "geometriesRunId": "vector-fixture-regions-run",
  "timePoint": "2024-06-30",
  "sourceDate": "2024-06-30",
  "areaCrs": "EPSG:6933",
  "evidenceClass": "observed_register",
  "caveat": "Synthetic fixture features; not an observation of any real place.",
  "countingRule": "Per class: clipped area of the unioned features; features counted when they intersect the region; whole-feature attribute values never apportioned.",
  "overlapPolicy": "union",
  "classification": {"column": "kind", "values": ["park", "forest"]},
  "measures": [
    {"indicatorId": "sum-park-area", "measure": "area-m2", "class": {"column": "kind", "values": ["park"]}},
    {"indicatorId": "count-park", "measure": "count", "class": {"column": "kind", "values": ["park"]}},
    {"indicatorId": "count-park-within", "measure": "count", "relation": "within", "class": {"column": "kind", "values": ["park"]}},
    {"indicatorId": "sum-forest-area", "measure": "area-m2", "class": {"column": "kind", "values": ["forest"]}},
    {"indicatorId": "distinct-state-count", "measure": "distinct-attribute", "attribute": "state"}
  ]
}
```

Project specifications live with the project's processing plan, not in the toolkit.

Tests: `tests/test_vector_aggregate.py`. The synthetic layers cover:

- union compared with sum;
- clipping;
- every relation;
- whole-feature sums with NaN;
- distinct values;
- `area-ha`, and both area CRSs;
- specification and column refusals;
- batches, all geometries and resume;
- `consolidate` and `parse_outputs` end to end;
- mocked catalogue checks.
