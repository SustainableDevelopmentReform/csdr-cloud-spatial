# CSDR Cloud Spatial Data Repository

This project is... TODO: Complete basic description.

## Installation

Create Python environment with GDAL and install the dependencies

First, ensure that GDAL is installed in your environment. To install the dependencies for this project, you can use pip with the pyproject.toml file:

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


### Pre commit hooks

Formats Python, YAML, and JSON.

To use pre-commit to automatically run ruff, mypy and other checks on each commit, make sure the development dependencies are installed and then run:

`pre-commit install`

Note that you will need to run `pre-commit run --all-files` if any of the hooks in `.pre-commit-config.yaml` change.


## Pipeline

TODO: Detail this section.

See the Makefile in this project for commands.
Reads/writes locally or to S3.
Please see here for docs on geometries, datasets, and products.
Provenance.


## Build and Push Workflow

This workflow builds a Docker container image and pushes it to Amazon ECR in two scenarios:

- **Main Branch:** A merge to `main` automatically triggers the workflow. The image is tagged with a version (based on git tags) and additionally gets a `latest` tag.

- **Manual Trigger:** Developers can manually trigger the workflow (using `workflow_dispatch`) to create and push a test build from their feature branch without the `latest` tag.

**Note:** If you build from a feature branch, you will need to visit AWS Batch and create a new revision of the job definition `csdr-dev-env-csdr-cloud-spatial` that uses your custom container image tag.

## Building and Running the Docker Image

To build the Docker image locally using [Buildx](https://docs.docker.com/buildx/working-with-buildx/), run:

```bash
docker buildx build . --tag csdr-cloud-spatial:latest
```

Once built, you can run the container:

```bash
docker run -it --rm csdr-cloud-spatial:latest
```
