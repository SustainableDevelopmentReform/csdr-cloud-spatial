# Raster to COG and STAC

`csdr convert raster-to-stac` turns one band of a GeoTIFF into Cloud Optimised GeoTIFFs
(COGs) with one STAC item each and a STAC collection, and records a provenance step. The
existing `csdr stac index` then writes the `stac-geoparquet` dataset that
`csdr products process-geometry` measures (`sum-<x>-area`, `percent-<x>-area`). The engine
is `csdr/raster_stac.py`.

The raster is never warped: the COGs keep the source CRS, resolution, pixel grid, data
type and nodata. A class area is a count of native cells times the native cell area, so a
dataset converted this way is measured on the grid it was published on.

## Command

```bash
csdr convert raster-to-stac \
  --source <path or s3:// or https:// URL to .tif|.tiff> \
  --target-location <dir> --asset-name <band key> --datetime <ISO date> \
  [--collection <id>] [--target-filename <slug>] [--band N] \
  [--tile-size <pixels> | --tile-degrees <degrees>] \
  [--classification-table <.dbf|.csv|.json|.aux.xml> [--class-value-column C] \
     [--class-name-column C] [--class-extra-columns a,b]] \
  [--display-copy <dir> [--display-crs <EPSG code>] [--display-resolution R] [--display-resampling M]] \
  [--nodata V] [--source-sha256 <64 hex>] [--item-properties '<json>'] \
  [--overview-resampling M] [--num-threads N] [--overwrite/--no-overwrite]
```

| Option | Behaviour |
|---|---|
| `--source` | One GeoTIFF. Local paths are read in place; store URLs are streamed to a temporary directory (normal credential chain), hashed and removed afterwards, so the machine needs disk for one copy. |
| `--target-location` | Folder for `cogs/`, `items/` and `collection.json`. A local path is made absolute, because item hrefs must be absolute for `stac index` and `odc.stac` to resolve them. |
| `--asset-name` | The STAC asset key. `process-geometry` loads the band under this name, so it is the `indicator-name` of the product's indicators. |
| `--datetime` | Item datetime: an ISO 8601 date or datetime (`2023-01-01`, `2020-07-01T00:00:00+10:00`); stored in UTC. A bare year or year-month means its first day. One run has one datetime; run once per epoch. |
| `--collection` | Collection id written to every item and `collection.json`. Default: the slug. A target already holding a different collection id is refused before anything is written; the same id extends it (see below). |
| `--target-filename` | Slug for file names and item ids. Default: the source file name without extension. |
| `--band` | Source band (1-based). Each run writes single-band COGs. |
| `--tile-size` | Tile edge in pixels. Default: one item for the whole raster. |
| `--tile-degrees` | Convenience for `--tile-size`: the pixel counts spanning this many degrees of longitude and of latitude at the raster centre, measured in the source CRS. Exact for a geographic CRS, approximate away from the centre for a projected one. The resolved pixel sizes are recorded in the step and a method note. |
| `--classification-table` | Class table for the asset's `classification:classes` (see below). Its SHA-256 is recorded. |
| `--display-copy` | Folder for `<slug>_display.tif`, one coarser COG of the whole raster for map display. |
| `--display-crs` | Reproject the display copy to this CRS (for example `EPSG:3857` for web maps). Default: the source CRS, no reprojection. Only the display copy is ever reprojected; the analysis COGs keep the source grid. Needs `--display-copy`. |
| `--display-resolution` | Display pixel size in source CRS units (display CRS units with `--display-crs`). Default: the coarser of 250 m (about 0.00225 degrees for a geographic CRS) and 10 x the source resolution. Must be coarser than the source. |
| `--display-resampling`, `--overview-resampling` | GDAL resampling names. Default: `mode` for integer data (keeps class codes), `average` for floating point. |
| `--nodata` | Nodata for the outputs. Sets one when the source declares none. Replacing a source value is allowed and recorded as a method note, because cells equal to the old value become data. Must fit the data type. |
| `--source-sha256` | Declared hash, verified against the bytes read before anything is written. The computed hash is always recorded. |
| `--item-properties` | JSON object merged into every item's `properties` (for example an epoch label). `datetime` is refused here. |
| `--num-threads` | GDAL `NUM_THREADS` for compression and overviews. Default `ALL_CPUS`. |
| `--overwrite` | Default on: tiles are rewritten, and items of this slug and collection that the run did not write (an earlier tiling, or a tile that is now all nodata) are removed with their COGs (`stale_items_removed`). `--no-overwrite` keeps tiles whose COG and item both exist and converts the rest, so an interrupted run resumes; earlier items are kept and counted with a warning (`stale_items_kept`), and an existing item whose `proj:shape` differs from this run's tile (another tiling with the same ids) is refused. |

## Tiles

Tiles are windows aligned to the source pixel grid, starting at the top-left pixel; the
last row and column of tiles are clipped to the raster. Every pixel is in exactly one
tile, so tiles never overlap and their union is the source extent. Tile edges are pixel
edges, not whole degrees: `--tile-degrees` only sets the size.

A tile without a single finite, non-nodata cell is not written; the step counts these as
`skipped_all_nodata_tiles`. The check reads the tile in strips and stops at the first valid
cell. A source with no valid cell at all is refused.

Each tile is copied from a windowed VRT straight into a COG (GDAL `COG` driver,
`COMPRESS=DEFLATE`, `BLOCKSIZE=512`, `OVERVIEWS=AUTO`, `PREDICTOR=YES` for floating point,
`BIGTIFF=IF_SAFER`), so a tile never has to fit in memory. Overviews are built when a tile
is larger than one block.

Files:

```
<target>/cogs/<slug>_<row>_<col>.tif     one COG per tile with data
<target>/items/<slug>_<row>_<col>.json   one STAC item per COG
<target>/collection.json                 the collection
<display>/<slug>_display.tif             optional display copy
```

Row and column indices are zero-padded to the same width within one run.

**One tiling per target and slug.** `stac index` is given every file in `items/`, so the
folder must hold exactly one tiling of each slug. Re-tiling into the same target with
`--overwrite` (the default) removes the earlier tiling's items and COGs of that slug and
collection; other slugs (other epochs) are left alone. To keep an earlier tiling, use a
fresh target.

## STAC items and collection

Items are made with `rio_stac.create_stac_item` (as `gmw extract` does), STAC 1.1:

- `datetime` from `--datetime`, `collection` and a `collection` link to `collection.json`,
  and a `self` link;
- the `proj` extension (`proj:epsg`, `proj:shape`, `proj:transform`, `proj:bbox`), which is
  what lets `process-geometry` load the native grid without a `crs`/`resolution` override;
- one asset under `--asset-name`, type `image/tiff; application=geotiff;
  profile=cloud-optimized`, role `data`, with `raster:bands` (`data_type`, `nodata`, with
  `"nan"` for NaN, statistics and histogram from the overviews);
- with a classification table, `classification:classes` on the asset.

`collection.json` holds the union of the item bounding boxes, the temporal extent, licence
`other` and an `item_assets` entry for the asset (data type, nodata, classes). A second run
into the same target with the same `--collection` (another epoch, another slug) extends the
spatial and temporal extent instead of replacing it.

## Classification tables

| Format | Read with |
|---|---|
| `.dbf` (e.g. `<raster>.tif.vat.dbf`) | `geopandas.read_file(path, ignore_geometry=True)` (OGR) |
| `.csv` | pandas |
| `.json` | a list of objects, e.g. `[{"value": 1, "name": "Forest", "group": "woody"}]` (or `{"classes": [...]}`) |
| `.aux.xml` (GDAL PAM) | the band's `GDALRasterAttributeTable`; linear-binned tables get their values from `Row0Min` and `BinSize` |

- `--class-value-column` (default `Value`) holds the cell value. Values must be integers and
  unique.
- `--class-name-column` holds the label. Default: the first column named `name`, `label`,
  `class` or `title`, then the first column whose name ends in `name` (for example a
  `<prefix>Name` column of a value attribute table). Column names match case-insensitively
  when there is no exact match.
- `--class-extra-columns` copies more columns into each class under their own names (for
  example grouping fields used to build `indicator-value` lists). Missing values are left out.
  A column named like a class field (`value`, `name`, `title`, `description`, `color_hint`,
  `nodata`, `percentage`, `count`) is refused. A column named `color_hint` (six hex digits,
  optional `#`) is always carried as the class's `color_hint`.

Each class is `{"value", "name", "title", ...extras}`. The classification extension
(v2.0.0) requires `name` to use only letters, digits, `-` and `_`, so `name` is the label
with other characters replaced by `-` (`class-<value>` when the label is empty) and the
original label is kept as `title`. A class whose value equals the nodata value is marked
`"nodata": true`.

**Coverage check.** For integer rasters, the distinct values of every COG written in
the run are collected and compared with the table. Values without a class (nodata
excluded) are recorded as `raster_values_without_class` (`count`, the first 20 `values`,
`tiles_checked`) and logged as a warning. Table values absent from the raster are fine.
This costs one extra read of each written COG. Floating-point rasters are not checked.

The classes describe the band; they do not select anything. A product still selects
classes with `indicator-value` (a value or comma list), as before.

## Display copy

`--display-copy` reads the whole band at the display resolution (GDAL resampling, which
uses source overviews when present), writes one COG and records its URL, the written
pixel size (`display_resolution`, x and y; whole pixels over the exact source bounds, so it
can differ slightly from the request), the requested size (`display_resolution_requested`)
and the CRS. By default it stays in the source CRS. With `--display-crs` the small, already
resampled array is reprojected to that CRS (same resampling method), so the
full-resolution data is never warped; the default resolution is then the coarser of 250 m
and 10 x the source resolution expressed in the display CRS. Cells outside the source take
the nodata value (0 when the source has none). The display copy is for map display only
and never a measurement input; the analysis COGs are never warped.

## Provenance

One `write_step`:

- `inputs`: `source`, `source_sha256`, `source_crs`, `source_resolution`, `source_shape`,
  `data_type`, `source_nodata`, `nodata`, `tile_size_pixels`, `tile_degrees`,
  `classification_table`, `classification_table_sha256`, `band`, `asset_name`, `datetime`,
  `collection`, the class column options, `item_properties`, the resampling options,
  `overwrite`, and `method_notes` (no warping, tile alignment and skipping, resolved
  `--tile-degrees`, nodata set or replaced, display copy use).
- `outputs`: `cogs_count`, `items_count`, `items_prefix`, `cogs_prefix`, `collection_url`,
  `tiles_planned`, `skipped_all_nodata_tiles`, `reused_items` (resume),
  `stale_items_removed`, `stale_items_kept` (with `--no-overwrite`), `class_count`,
  `raster_values_without_class`, `display_copy_url`, `display_resolution` (written, x and
  y), `display_resolution_requested`, `display_crs`.

Pass the same hash to `csdr provenance dataset --source-sha256` (see
[provenance-passthrough.md](provenance-passthrough.md)).

## From items to a measured product

```bash
# 1. Convert: a categorical raster, 1 degree tiles, classes from its value attribute table.
csdr convert raster-to-stac --source ./cache/datasets/classes/0-0-1/raw/classes.tif \
  --target-location ./cache/datasets/classes/0-0-1 --asset-name classes \
  --datetime 2023-01-01 --tile-degrees 1 \
  --classification-table ./cache/datasets/classes/0-0-1/raw/classes.tif.vat.dbf \
  --class-extra-columns group --display-copy ./cache/datasets/classes/0-0-1/display

# 2. Index the items (unchanged command).
csdr stac index \
  --source-stac-items "$(ls $PWD/cache/datasets/classes/0-0-1/items/*.json | paste -sd, -)" \
  --target-location ./cache/datasets/classes/0-0-1 --target-filename classes

# 3. Declare the dataset.
csdr provenance dataset --id <dataset-id> --dataset-type stac-geoparquet \
  --dataset-url ./cache/datasets/classes/0-0-1/classes.parquet \
  --source-metadata-url <url> --source-sha256 <hash from the step>

# 4. Measure: area of classes 3, 7 and 12, cell-centre rule.
csdr products process-geometry ... --raster-cell-centre \
  --indicators-to-extract '{"sum-selected-area": {"indicator-name": "classes", "indicator-value": "3,7,12"}}'
```

A continuous raster as one item:

```bash
csdr convert raster-to-stac --source ./cache/datasets/index/0-0-1/raw/index.tif \
  --target-location ./cache/datasets/index/0-0-1 --asset-name index --datetime 2023-01-01
```

Several epochs of one product into one collection (one run per epoch):

```bash
for epoch in 2010 2015 2020; do
  csdr convert raster-to-stac --source ./cache/datasets/epochs/0-0-1/raw/map_$epoch.tif \
    --target-location ./cache/datasets/epochs/0-0-1 --asset-name classes \
    --collection epochs --datetime $epoch-07-01 \
    --item-properties "{\"epoch\": \"$epoch\"}" \
    --classification-table ./cache/datasets/epochs/0-0-1/raw/map_$epoch.tif.aux.xml
done
```

`process-geometry --datetime-string-match <year>` then selects the epoch's items. The Makefile has
runnable targets (`convert-raster-*-local`).

## Performance and limits

- Memory is bounded by GDAL's block cache (`GDAL_CACHEMAX`) and one strip of the
  all-nodata check (16 M cells), not by tile size. Set `GDAL_CACHEMAX` (for example
  `GDAL_CACHEMAX=8192` MB) on a large machine; compression uses all cores by default.
- Each tile is read for the valid-cell check (usually one strip) and by the COG copy
  (GDAL may read the source again to build overviews), and each written COG is read once
  more for the coverage check when a classification table is given. A source striped one
  row per block is read efficiently in full-width strips.
- A store URL source is staged to local disk first (for hashing and random access).
- Only north-up rasters (no rotation terms) are supported. Masks other than a nodata value
  (internal mask bands, alpha) are not read; set `--nodata` if needed.
- Colour tables are not copied. Use `classification:classes` for legends: a `color_hint`
  column in the table (six hex digits) is carried into each class.
- Items are not validated against remote schemas at run time; the tests validate them
  offline against the `proj`, `raster` and `classification` extension schemas.
