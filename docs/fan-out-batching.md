# Fan-out batching

`process-geometry` processes geometries one per invocation. With hundreds of small
geometries, one pod per geometry spends more time starting than working. Batching runs
several geometries in one process, with one Dask client, and keeps one output file per
geometry so `consolidate`, `--no-overwrite` and the outputs are unchanged.

## Commands

```bash
csdr products list-geometries-years --geometry-provenance-url <url> --years 2023,2024 \
  --batch-size 20 --out-file /tmp/items.json
# [{"year": "2023", "ids": "id1,id2,...,id20"}, {"year": "2023", "ids": "id21,..."}, ...]

csdr products process-geometry ... --datetime 2023 --geometry-ids id1,id2,...,id20
```

- Without `--batch-size` the list keeps its per-id shape `[{"year": y, "id": i}]`.
- `--geometry-id` and `--geometry-ids` are mutually exclusive; ids in a batch must be
  unique and non-blank.
- Ids whose output already exists are skipped before any input is read (`--no-overwrite`
  is the default). A batch that fails part-way therefore resumes where it stopped when
  retried. A fully finished batch exits without reading anything.
- A failing geometry fails the whole invocation (non-zero exit), so the orchestrator
  retries it; the ids finished before the failure are not recomputed.
- Each output JSON records the command line as run. `consolidate` templates a batched
  command as `--geometry-ids {geometry_ids}` in its summary step.

## Argo engine

In the product engine template the list step's output feeds `withParam`, and each item
becomes one pod:

```yaml
- - name: list
    template: list-geometries-years      # csdr products list-geometries-years --batch-size {{workflow.parameters.batch-size}} --out-file /tmp/items.json
- - name: process
    template: process-geometry
    withParam: "{{steps.list.outputs.parameters.items}}"   # valueFrom: path: /tmp/items.json
    arguments:
      parameters:
        - {name: ids, value: "{{item.ids}}"}
        - {name: year, value: "{{item.year}}"}
# process-geometry container args:
#   csdr products process-geometry ... --geometry-ids={{inputs.parameters.ids}} --datetime={{inputs.parameters.year}}
```

The unbatched template keeps `{{item.id}}` and `--geometry-id`. Batching also shortens
the item list held in the workflow object.

## Choosing the batch size

The rule is minutes of work per pod: enough geometries that pod start-up and image pull
are a small fraction of the run, few enough that a retry repeats little work and the pod
still fits its memory request. Size memory for the largest geometry in a batch (large
geometries are tiled inside `process-indicators`, but they still dominate). Mixed sets
of a few very large and many small geometries are better split into two runs, the large
ones unbatched.
