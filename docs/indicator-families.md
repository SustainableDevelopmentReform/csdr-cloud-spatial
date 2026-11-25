# Indicator families

Indicator ids (the keys of `--indicators-to-extract`, equal to the app's measured
indicator ids) are validated against a registry in `csdr/indicators.py`:

- `INDICATOR_FAMILIES`: a compiled regex per family, the handler that computes it in
  `process_indicators_for_geometry` (`csdr/products.py`), a description, and an optional
  dependency on another family.
- `KNOWN_INDICATORS`: the exact ids accepted before families existed, kept for backwards
  compatibility (still importable from `csdr.cli_products`). Every one matches a family.

An id is accepted when it matches a family or is in the allow-list. An unknown id is
refused with the list of families and exact ids. A new indicator id that follows a family
(for example `sum-kelp-area`) needs no code change. Print the registry with:

```bash
csdr helpers indicator-families          # table
csdr helpers indicator-families --json   # {"families": [...], "exactIds": [...]}
```

| Family | Status | Computes |
|---|---|---|
| `sum-<x>-area` | implemented | Area in m² of selected raster classes within the geometry, or of the vector features that intersect it |
| `percent-<x>-area` | implemented | `sum-<x>-area` as a percentage of the geometry area; depends on `sum-<x>-area` in the same run |
| `count-<x>` | implemented | Features intersecting the geometry (the building footprints bounding-box index) |
| `mean-<x>`, `p10-<x>`, `p25-<x>`, `p50-<x>`, `p75-<x>`, `p90-<x>` | implemented | Mean and percentiles of valid raster cells (`--percentile-method linear\|histogram`) |
| `valid-fraction-<x>` | implemented | Valid cells divided by cells inside the geometry and the source extent |
| `distinct-<x>-count` | implemented | Distinct values among valid cells, optionally restricted to `indicator-value` |

The eight statistic families share the handler `zonal-statistics`: one pass per raster band
and geometry computes all of them, over the union of a multipolygon's parts. Definitions,
the cell rules, the percentile formulas and the output metadata are in
[zonal-statistics.md](zonal-statistics.md).

A **declared** family (handler `None`) validates on the command line, so templates and
catalogue entries can be prepared, but processing stops before reading any source with
`indicator family <x> is registered but has no handler in this build`. No shipped family is
declared today; the mechanism stays for families a later build adds. Adding a handler
means setting the family's `handler` and adding its branch in
`process_indicators_for_geometry`, with tests.

Dependent families run after the family they depend on (`dependency_stage`), whatever
order the JSON lists them in; ids within a stage run in sorted order.
