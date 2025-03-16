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
