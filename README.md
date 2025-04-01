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

### Reproducing the pipeline

You can compute all datasets and geometries by running:

```bash
dvc repro
```

### Datasets

You can compute all datasets by running:

```bash
dvc repro datasets/dvc.yaml
```

You can also compute a specific dataset by running:

```bash
dvc repro datasets/<dataset-name>/dvc.yaml

# For example
dvc repro datasets/global-mangrove-watch-annual-extent/dvc.yaml
```

### Geometries

You can compute all geometries by running:

```bash
dvc repro geometries/dvc.yaml
```

You can also compute a specific geometry by running:

```bash
dvc repro geometries/<geometry-name>/dvc.yaml

# For example
dvc repro geometries/abs-asgs-edition-3/dvc.yaml
```

### Using outputs

There is an example of how to use the outputs in the `examples` folder. You must have run the pipeline at least once before using the examples.

- [Global Mangrove Watch + ABS ASGS States](examples/global-mangrove-watch/abs-asgs-ste.ipynb)

## Build and Push Workflow

This workflow builds a Docker container image and pushes it to Amazon ECR in two scenarios:

- **Main Branch:** A merge to `main` automatically triggers the workflow. The image is tagged with a version (based on git tags) and additionally gets a `latest` tag.

- **Manual Trigger:** Developers can manually trigger the workflow (using `workflow_dispatch`) to create and push a test build from their feature branch without the `latest` tag.

**Note:** If you build from a feature branch, you will need to visit AWS Batch and create a new revision of the job definition `csdr-dev-env-csdr-cloud-spatial` that uses your custom container image tag.
