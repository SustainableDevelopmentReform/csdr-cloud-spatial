# Vector conversion

`csdr convert vector-to-geoparquet` turns one vector file into the toolkit's GeoParquet
contracts with a provenance step. It replaces per-dataset conversion code for vector
sources: a geometry set (with `csdr-id`/`csdr-name`) or a `geoparquet` dataset that
`csdr products process-geometry` can measure (`<dir>/<slug>.parquet` plus
`<dir>/partition/*.parquet`). The engine is `csdr/vector_convert.py`;
`csdr convert zip-to-parquet` uses the same engine.

## Command

```bash
csdr convert vector-to-geoparquet \
  --source <path or s3:// or https:// URL to .shp|.zip|.gpkg|.geojson|.json|.parquet> \
  --target-location <dir> [--target-filename <slug>] \
  [--zip-member <path in zip>] [--layer <layer>] [--where "<SQL WHERE>"] [--keep-columns a,b] \
  [--filter-geometry <vector file> [--clip]] \
  [--name-field <column> [--geometry-id <seed>]] \
  [--layer-column-value <value> [--append]] \
  [--partition [--grid-chunks N]] [--create-pmtiles] \
  [--source-sha256 <64 hex>] [--make-valid/--no-make-valid] [--overwrite/--no-overwrite]
```

| Option | Behaviour |
|---|---|
| `--source` | One file. Local paths are read in place; store URLs are streamed to a temporary directory (normal credential chain) and removed afterwards. A bare `.shp` brings its `.shx`, `.dbf`, `.prj` and `.cpg`. A directory or prefix is not a source. |
| `--target-filename` | Output slug. Default: the source file name (or the zip member's) without extension. |
| `--zip-member` | Path inside a `.zip` to read. Default: the only shapefile, GeoPackage or GeoJSON member; several members must be named. |
| `--layer` | Layer of a multi-layer source (GeoPackage). A multi-layer source without `--layer` is refused, listing the layers. |
| `--where` | SQL WHERE clause applied while reading. File formats use OGR SQL (`where_engine: ogr-sql`); GeoParquet uses SedonaDB SQL (`where_engine: sedonadb-sql`), because the GDAL in the Python wheels has no Parquet driver. Simple predicates (`=`, `<>`, `IN`, `LIKE`, `AND`, `OR`) mean the same in both. An empty selection is refused. |
| `--filter-geometry` | A local path or store URL of any supported vector file. Its features are reprojected to the source CRS, repaired and unioned; only source features intersecting the union are kept. The filter's URL and SHA-256 and the row counts before and after are recorded. Use it to select a region spatially when the source has no attribute to filter on. No intersecting feature is refused. |
| `--clip` | Only with `--filter-geometry`: also clip each kept geometry to the union, then repair it. Polygon features keep polygonal parts only, so a feature that merely touches the filter (a line or point intersection) is dropped. Without `--clip`, kept features stay whole (for example whole-feature attributes that must not be apportioned). |
| `--keep-columns` | Attribute columns to keep. Geometry and `--name-field` are always kept. Unknown columns are refused, listing those available. |
| `--name-field`, `--geometry-id` | Geometry sets: add `csdr-name` (must be non-blank) and `csdr-id`, exactly as `zip-to-parquet` does. |
| `--layer-column-value` | Add a constant `layer` column, so several sources can share one dataset. Refused if the source already has a `layer` column. |
| `--append` | Needs `--layer-column-value`. Reads the existing `<slug>.parquet`, removes its rows whose `layer` equals the value (a retried conversion replaces its own rows), adds the new rows and rewrites the file. Existing partitions for this slug are then rewritten in full (see `--partition`). The CRS must match the existing file. |
| `--partition`, `--grid-chunks` | Write `partition/<slug>_<lon>_<lat>.parquet` on a centroid grid of `2N x N` cells (10 = 18 degrees, 40 = 4.5 degrees). Existing partition files are replaced. If `partition/` already holds this slug's files, they are always rewritten after a write (append or overwrite), whether or not `--partition` is passed, so `process-geometry` never measures stale rows; this is logged and recorded as `partition_rewritten: true`. The rewrite uses `--grid-chunks` (default 10), so pass the same value as before. |
| `--create-pmtiles` | Write `<slug>.pmtiles` with tippecanoe (same flags as `zip-to-parquet`). Tiles keep `csdr-id`, `csdr-name` and `layer` when present. |
| `--source-sha256` | Declared hash, verified against the bytes read before anything is written. The computed hash is always recorded. |
| `--make-valid` | Default on: invalid geometries are repaired with shapely `make_valid(method='structure', keep_collapsed=False)`. A Polygon or MultiPolygon feature keeps polygonal parts only: line and point slivers of a GeometryCollection are dropped. Repair also runs after every reprojection (the EPSG:4326 partition and tile copies) and after `--clip`. `--no-make-valid` keeps invalid source geometries and records the count. |
| `--overwrite` | Default on. With `--no-overwrite` and an existing `<slug>.parquet` nothing is written and the step records the skip. The source is not read, except a `.zip` without `--zip-member` and `--target-filename`, which is read first to resolve the member that names the output. |

## One dataset per target folder

The product engine reads every file under `<target>/partition/`, so a target folder holds
one partitioned dataset. A conversion that would partition (with `--partition`, or because
its slug already has partitions) is refused when `partition/` holds another slug's files:
`One dataset per target folder; found partitions for <other-slug>`. Give each dataset its
own `--target-location`. Several sources become one dataset with `--layer-column-value`
and `--append`.

## Coordinate reference systems

- `<slug>.parquet` keeps the source's native CRS, as `zip-to-parquet` always has.
- Partition files are an EPSG:4326 copy. The product engine intersects partitions with
  EPSG:4326 boundaries (SedonaDB refuses mismatched CRS arguments), so the `geoparquet`
  dataset contract requires it. Area is measured in EPSG:6933.
- The product engine sums the whole area of every partition feature that intersects a
  boundary; it does not clip. When only the part of each feature inside a region should
  count, clip at conversion time with `--filter-geometry <region> --clip`.
- PMTiles are built from a temporary EPSG:4326 copy, because tippecanoe expects WGS 84.
- The geometry column is always named `geometry` in the outputs.

## Provenance

The command writes one `write_step`:

- `inputs`: `source`, `source_sha256`, `source_member_sha256` (bare shapefile sidecars),
  `zip_member`, `layer`, `where`, `where_engine`, `filter_geometry`,
  `filter_geometry_sha256`, `clip`, `rows_before_filter`, `rows_after_filter`,
  `keep_columns`, `name_field`,
  `geometry_id`, `layer_column_value`, `append`, `grid_chunks`, `make_valid` and
  `method_notes` (geometry repair counts and method, clipping, CRS of partitions and
  tiles).
- `outputs`: `target_url`, `row_count`, `crs`, `dropped_empty_geometries`,
  `replaced_rows` (append), `partition_count`, `partition_prefix`,
  `partition_rewritten`, `pmtiles_url`.

Features whose geometry is null or empty after repair and clipping are dropped before
writing (they have no area and would become empty geometry outputs); the count is logged
and recorded as `dropped_empty_geometries`.

Prefer a zipped shapefile when one hash should identify every member. Pass the same
hash to `csdr provenance dataset --source-sha256` (see
[provenance-passthrough.md](provenance-passthrough.md)).

## Examples

A dataset measured by `process-geometry`:

```bash
csdr convert vector-to-geoparquet --source ./cache/datasets/example/0-0-1/raw/features.zip \
  --target-location ./cache/datasets/example/0-0-1 --target-filename features --partition
csdr provenance dataset --id <dataset-id> --dataset-type geoparquet \
  --dataset-url ./cache/datasets/example/0-0-1/features.parquet \
  --source-metadata-url <url> --source-sha256 <hash from the step>
# then: csdr products process-geometry ... --indicators-to-extract '{"sum-feature-area": {"indicator-name": "feature"}}'
```

Rows of a large-area source filtered to one region's boundary (the source needs no
region attribute); whole features are kept, so per-feature attributes are not
apportioned:

```bash
csdr convert vector-to-geoparquet --source ./cache/datasets/projects/0-0-1/raw/projects.parquet \
  --target-location ./cache/datasets/projects/0-0-1 --target-filename projects \
  --filter-geometry ./cache/geometries/region/0-0-1/region.parquet --partition
```

Add `--clip` when only the part inside the boundary should be measured.

One dataset from three sources, one `layer` value each:

```bash
for layer in layer_a layer_b layer_c; do
  csdr convert vector-to-geoparquet --source ./cache/raw/$layer.shp \
    --target-location ./cache/datasets/combined/0-0-1 --target-filename combined \
    --layer-column-value $layer --append --partition
done
```

A geometry set filtered at read time, with tiles:

```bash
csdr convert vector-to-geoparquet --source ./cache/geometries/regions/0-0-1/raw/regions.zip \
  --target-location ./cache/geometries/regions/0-0-1/runs/<run-id> \
  --where "REGION_CODE = '1'" --name-field REGION_NAME --create-pmtiles
```

The Makefile has runnable targets over the test fixtures (`convert-vector-*-local`).

## `zip-to-parquet`

The interface is unchanged. The zip member may now be a GeoPackage (single layer) or
GeoJSON as well as a shapefile; the output is `<member name>.parquet`. Its historical
behaviour is kept: no geometry repair, no partitions, PMTiles with `csdr-id` and
`csdr-name` only. Its step additionally records `source_sha256`, and its PMTiles are now
built from an EPSG:4326 copy: the EPSG:4326 and EPSG:7844 sources existing recipes use
produce identical tiles. Unlike `vector-to-geoparquet`, it keeps null or empty
geometries, as before.

## Limits

- The whole selection is held in memory (geopandas). Size the machine for the decoded
  geometries, and use `--where` and `--keep-columns` to reduce them.
- A Parquet source must be GeoParquet (geometry metadata). Partitioned Parquet
  directories are not sources.
- PMTiles need `tippecanoe` on the path (present in the image).
