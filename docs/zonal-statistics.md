# Zonal statistics

`csdr products process-geometry` computes eight raster statistic families over a
`stac-geoparquet` dataset (COGs indexed by STAC items):

| Family | Value | Type |
|---|---|---|
| `mean-<x>` | float64 sum of the valid cells divided by the number of valid cells | float |
| `p10-<x>`, `p25-<x>`, `p50-<x>`, `p75-<x>`, `p90-<x>` | 10th, 25th, 50th, 75th, 90th percentile of the valid cells (`--percentile-method`) | float |
| `valid-fraction-<x>` | valid cells divided by all cells inside the boundary | float, 0 to 1 |
| `distinct-<x>-count` | number of distinct values among the valid cells, optionally restricted to `indicator-value` | integer |

`indicator-name` is the band (STAC asset key). `indicator-value` is accepted only by
`distinct-<x>-count`: a number or a comma-separated list of numbers (the class values that
may be counted). The other families use every valid cell and refuse an `indicator-value`.
Bad requests are refused before any source is read.

```bash
csdr products process-geometry \
  --product-id=<product> --run-id=<run> \
  --geometry-provenance-url=<geometries>.parquet.provenance.json \
  --dataset-provenance-url=<dataset>.parquet.provenance.json \
  --target-location=<dir> --datetime=2023 --geometry-id=<id> \
  --indicators-to-extract='{"mean-cond": {"indicator-name": "cond"}, "p50-cond": {"indicator-name": "cond"}, "valid-fraction-cond": {"indicator-name": "cond"}}' \
  --raster-cell-centre \
  --percentile-method=histogram --percentile-bins=1000 --percentile-range=0,1
```

```bash
# Distinct classes, counting only listed class codes (0 = background is not listed).
--indicators-to-extract='{"distinct-class-count": {"indicator-name": "class", "indicator-value": "3,5,7,11"}}'
```

## Cells

Each statistic uses the same cells as `sum-<x>-area` (`csdr/utils.py`):

- **Boundary cells.** The boundary is rasterised on the loaded grid with the same rule as
  the area sum: `--raster-all-touched` (default, every touched cell) or
  `--raster-cell-centre` (cells whose centre is inside). The boundary goes from EPSG:4326
  through EPSG:6933 to the grid CRS, vertices only (no densification).
- **Source extent.** Loaded cells outside every source raster hold the fill value, which
  looks like nodata. They are not boundary cells. The extent is the union of each STAC
  item's native grid (projection fields, as odc-stac reads them) or, when an item has
  none, its footprint geometry; a cell is inside when its centre is. A footprint can
  over-cover the raster (fill cells would then count as nodata), so its use is logged as a
  warning and recorded per band as `extentSource: "footprint"` (`"proj"` for native grids,
  `"none"` if an item has neither, in which case no extent is applied). Write STAC items
  with projection fields (`rio-stac` `with_proj=True`).
- **Valid cells.** Boundary cells whose value is finite and not the band's nodata value.
  NaN, +inf and -inf are never valid. A real zero is valid.

`valid-fraction` therefore counts nodata cells inside the boundary and the source extent
in its denominator, and leaves out cells outside the source extent.

The validity rule is shared with `sum-<x>-area`: a declared nodata value is never counted
as area either, and the value selection (`indicator-value`) applies to valid cells only.
Before this rule, `sum-<x>-area` without an `indicator-value` counted the cells of a
non-NaN nodata value (for example 255 in a byte raster) as area; such products change
when they are re-run.

**Missing coverage: the statistics raise, the area sum does not.** Support is checked over
the whole boundary first: no STAC item for any part, or no valid cell anywhere in the
boundary, raises (`NoValidRasterCellsError` for the latter). No statistic is ever reported
as 0 or NaN for a boundary without data, because a mean or a percentile of no cells is
undefined. `sum-<x>-area` keeps its existing behaviour: a part or a boundary with no STAC
item or no valid cell adds an area of 0.0 and logs a warning. A run that requests both
therefore fails on an uncovered boundary because of the statistics, not the sum. The load
must yield exactly one time slice; narrow `--datetime-string-match` otherwise.

## Percentile methods

`--percentile-method linear` (default): `numpy.quantile(values, q, method="linear")` over
the valid values in float64. With the sorted valid values `v[0..N-1]`, position
`h = q * (N - 1)` and the result is `v[floor(h)] + (h - floor(h)) * (v[floor(h)+1] - v[floor(h)])`.

`--percentile-method histogram`, with `--percentile-bins B` (default 1000) and
`--percentile-range lo,hi` (default `0,1`):

1. Each valid value `v` goes into bin `clip(trunc((v - lo) / (hi - lo) * B), 0, B - 1)`,
   computed in float64. Values below `lo` fall into bin 0 and values at or above `hi` into
   bin `B - 1`; the number of values outside `[lo, hi]` is recorded as
   `histogramClippedCells`.
2. With cumulative counts `C[i]` and `N` valid cells, `target = N * q`,
   `i = count(C < target)` (clipped to `[0, B - 1]`).
3. The value is the bin midpoint `lo + (i + 0.5) * (hi - lo) / B`.

This is the midpoint of the bin that holds the `ceil(q * N)`-th smallest valid value
(numpy `inverted_cdf`, snapped to the bin midpoint). The result resolves the value to one
bin width. The mean and the valid fraction never use the histogram.

Lazy (dask) and eager arrays give the same counts, histograms and values; the float64 sum
behind the mean can differ in the last bit between chunkings.

## One pass, multipart geometries

Every requested statistic of one band is computed from one pass per geometry part: the
counts, the float64 sum, the histogram (histogram method), the valid values (linear method)
and the distinct values (distinct count) are reduced together, so a lazy source is read
once per part. Bands are cached within the geometry run, so `mean-`, `p10-`, `p25-`,
`p50-`, `p75-`, `p90-` and `valid-fraction-` of one band share one pass.

A multipolygon (and a geometry tiled for size) is measured as the union of its parts, not
as a mean of part statistics: boundary cells, valid cells, sums, histograms and distinct
values are added up over the parts, and valid values are concatenated for linear
percentiles. A part with no valid cells still adds its boundary cells; a part outside
every source raster adds none. Parts do not overlap, so with the cell-centre rule each
cell belongs to at most one part. With all-touched, a cell touched by two parts is
counted in both, as in the area sum.

Memory: the histogram method and the distinct count keep only bins and distinct values.
The linear method keeps every valid value of the geometry (8 bytes per cell in float64
when the percentile is computed); prefer the histogram method for very large boundaries.

## Output metadata

The per-geometry output `metadata` (carried through `consolidate`, and by `parse_outputs`
into each output that `provenance product` posts) gains, when a statistic family is
requested:

| Key | Value |
|---|---|
| `rasterCellRule` | `all-touched` or `cell-centre` |
| `zonalCellCounts` | `{<band>: {"boundaryCells", "validCells"[, "histogramClippedCells"], "extentSource"}}`; `extentSource` is `proj`, `footprint` or `none` (the weakest over the parts) |
| `percentileMethod` | `linear` or `histogram` (only when a percentile is requested) |
| `percentileBins`, `percentileRange` | `B` and `[lo, hi]` (histogram method only) |

## Parity with a fixed-bin reference implementation

The histogram method and these cell rules reproduce a windowed reference implementation
that burns zones with the cell-centre rule, takes `isfinite(v) and v != nodata` as valid,
sums in float64 and uses 1000 bins over `[0, 1]`, a common design for index rasters
between 0 and 1. On synthetic 90 m grids with random polygons the counts, valid fraction
and all five percentiles match exactly and the mean within 2e-16. To reproduce such a run:

- load the COGs on their native grid (no `crs`/`resolution` in `--load-kwargs`),
- pass `--raster-cell-centre` and `--percentile-method=histogram` (defaults 1000, `0,1`),
- use the same boundary geometry.

Known differences: a cell whose centre lies within about 1e-4 m of the boundary can flip
because the boundary vertices round-trip through EPSG:4326 and EPSG:6933; loading on another
grid changes cell membership; the linear method differs from the bin midpoint by up to half
a bin plus the interpolation gap between neighbouring valid values.

## Tests

`tests/test_zonal_statistics.py`: small grids with a real zero, nodata, NaN, inf and valid
cells outside the boundary; hand-computed mean, valid fraction, linear and histogram
percentiles and distinct counts; chunked equals eager; cell centre against all-touched on a
boundary crossing a pixel column; source-extent fill cells; multipart union; no-coverage
refusals; ordering with `sum-`/`percent-`; a GeoTIFF indexed as STAC-GeoParquet run through
`process-geometry` and `consolidate`. `tests/test_raster_stac.py` runs `raster-to-stac`,
`stac index`, `process-geometry` with `sum-<x>-area` and the statistics, `consolidate` and
`parse_outputs` end to end. `tests/test_raster_measurement.py` and
`tests/test_multipart_raster_coverage.py` cover the shared validity rule and cell rules on
the area sum, including its 0.0 result for missing coverage.
