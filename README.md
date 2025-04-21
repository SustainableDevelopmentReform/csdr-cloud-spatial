# CSDR Cloud Spatial Data Repository

## Installation

To install the dependencies for this project, you can use pip with the pyproject.toml file:

### Using pip

```bash
# Install directly from pyproject.toml
pip install -e .
```

### Using Poetry

If you prefer using Poetry for dependency management:

```bash
# Install Poetry if you don't have it already
pip install poetry

# Install dependencies
poetry install
```

## Development

For development purposes, you can install the package with development dependencies:

```bash
# Using pip
pip install -e ".[dev]"

# Using Poetry
poetry install --with dev
```

## Pipeline

We are using [DVC](https://dvc.org/doc/start) to manage the pipeline, this allows, us to track the dependencies between the datasets and geometries, and chain multiple pipelines together.

See installation instructions on the [DVC website](https://dvc.org/doc/install).

### Check status of the pipeline

This will print a list of pipelines that have changed, and need to be recomputed. Note, it will not track input datasets (that need to be ingested), but it will track all other dependencies - including code, intermediary datasets and geometries, etc.

```bash
csdr dvc status
```

You can also filter by pipeline type:

- `csdr dvc status datasets`
- `csdr dvc status geometries`
- `csdr dvc status products`

### Reproducing the pipelines (locally)

You can compute all datasets and geometries by running:

```bash
dvc repro -P
```

**Note** if you do not have input/ingested datasets, these will all be downloaded locally - you can use the `--allow-missing` [flag](https://dvc.org/doc/command-reference/repro#--allow-missing) skip stages with no other changes than missing data

- This is useful if you are using existing intermediary datasets/geometries and only want to compute the products. Otherwise you will need to ingest and recompute the intermediary datasets/geometries, if the input data doesn't exist.

### Datasets

You can compute all datasets by running:

```bash
dvc repro -R datasets/
```

Note, you can also reproduce a specific pipeline by running:

```bash
dvc repro <pipeline-type>/<dataset-name>/dvc.yaml

# For example
dvc repro datasets/global-mangrove-watch-annual-extent/dvc.yaml
```

### Geometries

You can compute all geometries by running:

```bash
dvc repro -R geometries/
```

### Products

You can compute all products by running:

```bash
dvc repro -R products/
```

### Using outputs

There is an example of how to use the outputs in the `examples` folder.

- [Global Mangrove Watch + ABS ASGS States](examples/global-mangrove-watch/abs-asgs-ste.ipynb)

### Provenance

The `csdr dvc publish` command will generate a provenance file for each pipeline. This will include the pipeline file, the git commit hash, the git commit date, and the dependencies.

```bash
csdr dvc publish
```

**Note** this will also commit the changes to the git repository (it makes two commits, one before and one after the provenance generation).

### DVC Limitations

- You can't template params/variables in an entire repo - so there is no easy way to have a high-level variable and apply to all pipelines.
- Datasets stored on DVC Remotes can't be used in the same was as direct S3 access (eg zarr or geoparquet files).

## Build and Push Workflow

This workflow builds a Docker container image and pushes it to Amazon ECR in two scenarios:

- **Main Branch:** A merge to `main` automatically triggers the workflow. The image is tagged with a version (based on git tags) and additionally gets a `latest` tag.

- **Manual Trigger:** Developers can manually trigger the workflow (using `workflow_dispatch`) to create and push a test build from their feature branch without the `latest` tag.

**Note:** If you build from a feature branch, you will need to visit AWS Batch and create a new revision of the job definition `csdr-dev-env-csdr-cloud-spatial` that uses your custom container image tag.
