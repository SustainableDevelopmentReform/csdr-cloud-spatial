# Preparing pre-computed product values

`csdr products prepare-precomputed` copies explicitly selected JSON numbers into
the existing SDF product Parquet format. It does not rerun the originating method,
intersect geometries, allocate totals, round values, create catalogue records or
promote main runs. Use it when an external pipeline has already computed the
values. It complements the existing provenance and output import commands.

```sh
csdr products prepare-precomputed \
  --source /private/prepared/source.json \
  --spec /private/prepared/bindings.json \
  --output /private/prepared/new-run
```

The source and specification are local files, each bounded to 32 MiB. Set
`CSDR_API_HOSTNAME`, `CSDR_API_KEY` and, for API keys whose owner belongs to several
organisations, `CSDR_API_ORGANIZATION_ID` for read-only catalogue verification
(`csdr/catalogue.py`, with the same organisation header, membership check, timeouts
and no-redirect rule as posts). HTTPS is required except for loopback development.
Credentials and local source paths are not written into the preparation receipt.

The specification contains:

```json
{
  "version": 1,
  "productId": "existing-product",
  "productRunId": "new-run-id",
  "datasetRunId": "existing-dataset-run",
  "geometriesRunId": "existing-boundary-run",
  "sourceSha256": "<64 lowercase hexadecimal characters>",
  "role": "context",
  "method": {
    "name": "Original preparation method",
    "version": "1",
    "description": "Pre-computed externally; this step only copies selected values."
  },
  "reviewStatement": "State the actual review status and limitations.",
  "caveats": ["Do not allocate this regional total to individual properties."],
  "sourceProvenancePointers": ["/provenance"],
  "bindings": [{
    "geometryOutputId": "existing-region-id",
    "indicatorId": "existing-indicator",
    "timePoint": "2026-10-17T04:16:37Z",
    "valuePointer": "/regions/0/totalAud",
    "unit": "AUD",
    "evidenceClass": "linked",
    "temporalBasis": "A snapshot of whole-life commitments, not annual spending."
  }]
}
```

Bindings use JSON Pointers, not executable expressions. Every geometry,
indicator and time-point combination must be unique. Units must match the
measured indicators. Numbers are copied without coercion; zero remains zero,
explicit null is omitted and recorded, missing paths fail. Non-finite numbers,
booleans, numeric strings and numbers outside the API's safe numeric range fail.
Sparse Parquet indicator fields remain absent during normal output parsing.

The dataset run must have a `provenanceJson.precomputedSource` declaration with
`version: 1`, the same `sourceSha256`, and the same `role` (`measurement`,
`context` or `illustrative`). This declares the originating JSON, even when the
dataset's `dataUrl` is a normalized Parquet derivative. Record the derivative's
own checksum and preparation separately. Write the declaration with
`--provenance-extra` ([provenance-passthrough.md](provenance-passthrough.md)) when
the dataset run is created:

```sh
csdr provenance dataset --id <dataset-id> --dataset-url <file> --dataset-type parquet \
  --source-metadata-url <url> --post-to-database \
  --provenance-extra '{"precomputedSource": {"version": 1, "sourceSha256": "<64 hex>", "role": "context"}}'
```

The command checks the actual product parents, boundary output membership, units,
organisation and access decision (a `metadata.preparedViewAccess` on a run must be
`{"version": 1, "status": "available"}`). It requires a fresh product run ID and
checks the catalogue before and after the projection. It does not independently
certify source rights or scientific quality.

The new directory contains `product.parquet` and `method.json`. The receipt
retains the specification, original provenance fields, input/output checksums,
null bindings and catalogue-check digest. Per-row metadata retains the value
pointers, evidence classes, temporal meaning and limitations. Original files and
existing preparations are never overwritten. `--offline` is available for local
preparation review and marks the receipt `not-verified`.

## Parquet sources

`--source-format parquet` accepts a pinned Parquet file. Its rows are projected into the same JSON
Pointer space. Nothing is recomputed, and `sourceSha256` is still the SHA-256 of the source file's
bytes, so the dataset run declares the Parquet file's hash.

| Pointer form | Applies when | Example |
|---|---|---|
| `/rows/<index>/<column>` | No `--key-column`: `/rows` is the list of rows in file order. Use it to re-register bindings that were written by row index | `/rows/0/area_ha` |
| `/rows/<key>/<column>` | `--key-column <col>`: `/rows` is an object keyed by that column's values. This is the form for new projections and `bind-regions` templates | `/rows/region-101/area_ha` |
| `/rows/<key1>/<key2>/<column>` | `--key-column a,b`: one pointer level per key column, for long tables with one row per region and group | `/rows/region-101/parks/area_ha` |

Rules for keyed rows:

- Keys must be unique.
- Keys must be non-blank strings or integers. Integers become their decimal text.
- Duplicate or null keys are refused.
- Only numeric cells can be bound.
- Nulls are recorded as null bindings, NaN is refused, and other types are projected to strings.

`/schema/columns`, `/schema/metadata/<key>` (Parquet key-value metadata) and `/rowCount` can serve as
`sourceProvenancePointers`. The receipt adds `sourceFormat` and `sourceProjection` (the pointer form
and key columns). JSON receipts are unchanged.

```sh
csdr products prepare-precomputed --source table.parquet --source-format parquet \
  --key-column region_id --spec bindings.json --output ./new-run
```

## Generating bindings: `bind-regions`

`csdr products bind-regions` writes a bindings template with one binding per geometry output of a
geometries run. A binding file for hundreds of regions is then generated, not typed.

```sh
csdr products bind-regions --geometries-run-id <run> --key-column CODE \
  --key-template 'region-{key}' --out-file bindings-template.json
```

- **Key source.** `--key-source metadata` (the default) takes the key from the geometry output's
  `--key-column` attribute: `properties`, then `metadata`. `name` uses the output name
  (`csdr-name`), and `csdr-id` uses the geometry output id.
- **Key template.** `--key-template` rewrites each key so it matches the prepared source's key
  column, for example a `region-<code>` form.
- **Reading outputs.** Outputs are read through the catalogue
  (`GET /geometries-run/{id}/outputs`, paginated and bounded). With `--offline-geometries-url
  <parquet>`, the command reads the geometries parquet directly; `geometryOutputId` is then
  `csdr-id`.
- **Refusals.** Missing or duplicate keys are refused, and the offending outputs are listed.

The template has the exact specification fields, with `geometriesRunId`, every `geometryOutputId`
and `valuePointer: /rows/<key>/{{column}}` filled in. Every other decision (`{{indicatorId}}`,
`{{unit}}`, `{{timePoint}}`, the header fields and so on) is a `{{...}}` placeholder.
`prepare-precomputed` refuses a specification in which any value is still exactly one of these
placeholders, or a `valuePointer` still ends in `/{{column}}`. Free text that merely contains braces
(for example `{{workflow.name}}` in `reviewStatement`) is accepted. Copy the bindings once per
indicator and fill them in. A region that is missing from the source then fails as a missing
pointer, never as a zero.

After review, use the existing storage and provenance/output import
workflow. Register the original method as pre-computed, preserve the receipt,
and set the main run explicitly only after read-back verification. This command
does not provide atomic catalogue promotion or make mutable output rows immutable.
Prepared API views still need a pinned value digest and current access checks.

Tests: `tests/test_precomputed.py` and `tests/test_prepared_at_scale.py`. They cover
real Parquet read-back, the existing parser, input tampering, sparse and null fields,
concurrent catalogue changes, incorrect parent, geography, organisation, unit and
access declarations, keyed and indexed Parquet pointers, and `bind-regions` templates
from the catalogue and from a geometries parquet.
