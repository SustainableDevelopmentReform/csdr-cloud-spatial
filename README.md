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

You can compute all datasets and geometries by running:

```bash
dvc repro
```

### Datasets

You can compute all datasets by running:

```bash
dvc repro datasets/
```

You can also compute a specific dataset by running:

```bash
dvc repro datasets/<dataset-name>/dvc.yaml
```

### Geometries

You can compute all geometries by running:

```bash
dvc repro geometries/
```

You can also compute a specific geometry by running:

```bash
dvc repro geometries/<geometry-name>/dvc.yaml
```

### Using outputs

There is an example of how to use the outputs in the `examples` folder. You must have run the pipeline at least once before using the examples.
