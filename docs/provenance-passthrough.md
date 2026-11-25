# Provenance passthrough

`csdr provenance dataset|geometry|product` accept source identity and declarations, so
nothing in a provenance file has to be edited by hand.

| Option | Commands | Written as |
|---|---|---|
| `--source-sha256 <64 hex>` | dataset, geometry | `sourceSha256` (lower case) |
| `--source-version-id <id>` | dataset, geometry | `sourceVersionId` (for example an S3 VersionId or a release tag) |
| `--provenance-extra '<json object>'` | dataset, geometry, product | each key merged at the top level |

All three are optional and absent unless given. The values are written to the provenance
file and to `provenanceJson` in the run posted with `--post-to-database`, never to the
top-level run body, like the `workflowTemplate*` fields.

`--provenance-extra` must be a JSON object. Keys the writer, the run endpoints or the app
set themselves are refused: `id`, `dataType`, `dataEtag`, `dataSize`, `dataUrl`,
`dataPmtilesUrl`, `description`, `imageCode`, `imageTag`, `provenanceUrl`,
`provenanceUpdated`, `provenanceJson`, `workflowDag`, `workflowDagSimple`, `sourceUrl`,
`sourceMetadataUrl`, `sourceSha256`, `sourceVersionId`, `geometriesRunId`,
`datasetRunId`, `productRunId`, `datasetId`, `geometriesId`, `productId` and the
`workflowTemplate*` fields (`RESERVED_PROVENANCE_KEYS` in `csdr/provenance.py`).
Invalid input is refused before any file is written.

## Source identity

`csdr convert vector-to-geoparquet` records the SHA-256 of the bytes it read in its step
(`source_sha256`); pass the same value to `--source-sha256` so the run itself carries it
(see [vector-conversion.md](vector-conversion.md)). `--source-version-id` records which
object version or release was read when the source location is versioned.

## Declarations

A method that verifies a run later can read declarations from its `provenanceJson`.
Write them with the toolkit:

```bash
csdr provenance dataset --id <dataset-id> --dataset-url <file> --dataset-type parquet \
  --source-metadata-url <url> --source-sha256 <hash> --post-to-database \
  --provenance-extra '{"sourceRelease": {"version": 1, "edition": "2024", "licence": "CC-BY-4.0"}}'
```

The declaration's content is the declarer's responsibility; the toolkit only writes it
and refuses collisions with reserved keys.
