# University of New South Wales (UNSW) Centre for Sustainable Development Reform (CSDR) Cloud Spatial Data Toolkit

## Information

### Partners
This is a project by the University of New South Wales [Centre for Sustainable Development Reform](https://www.unsw.edu.au/research/centre-for-sustainable-development-reform) and [Auspatious](https://auspatious.com/).

### Description
This toolkit provides cloud-native geospatial data processing and workflow automation. It supports spatial data ingestion, transformation, calculation, and output, with provenance tracking for reliable and repeatable results.

### License
This project is licensed under the terms of the Apache License, Version 2.0. See the [LICENSE](LICENSE) file for details.

### Repositories
This toolkit is part of a larger system. Additional repositories for the application and workflow templates will be open-sourced and linked here when available. This toolkit is reliant on the app running either locally or hosted somewhere.

### Schema:
- **Datasets**: the data that will be analysed. Example: [Global Mangrove Watch's (GMW) global mangrove habitat extents](https://www.globalmangrovewatch.org/)
- **Geometries**: the data that will be used to segment/summarise/analyse datasets. Example: Global Exclusive Economic Zones (EEZ) (one multipolygon per country or territory).
- **Products**: A combination of any dataset and geometry. For example the GWM dataset and the EEZ geometry making the product "GMW per EEZ".
- **Indicators**: The one or more variables that are calculated for a product. For example, for the example product, an indicator would be the sum of mangrove habitat extent per EEZ.

Each of datasets, geometries, and products have runs. A single run is a workflow that calls commands from this toolkit. Each of these can have many runs. Each run has its own provenance and is written to the target store, and the app's PostgreSQL database.

### Types of datasets:

| # | Dataset Type                      | Data Class | Example                |
|---|-----------------------------------|------------|------------------------|
| 1 | STAC API or STAC-Geoparquet       | Raster     | DEP Seagrass           |
| 2 | Zipped TIFFs / COGs               | Raster     | GMW Mangrove Extents   |
| 3 | Partitioned Parquets              | Vector     | VIDA Buildings         |
| 4 | Zipped Shapefile                  | Vector     | ACA Reef Extents       |


### Data formats used:
- [SpatioTemporal Asset Catalogs (STAC)](https://stacspec.org/)
- [Cloud Optimized GeoTIFFs (COG)](https://cogeo.org/)
- [STAC-Geoparquet](https://stac-geoparquet.org/)
- [Parquet](https://parquet.apache.org/docs/overview/)
- [Arrow](https://arrow.apache.org/)
- [PMTiles](https://docs.protomaps.com/pmtiles/)

This pipeline toolkit also ingests zipped `.shp`s and `.tiff`s for interoperability.

### Provenance:
In this toolkit, each dataset, geometry, and product run is recorded with provenance that captures source inputs, processing context, and generated outputs, ensuring results are traceable, reproducible, and auditable across local and cloud workflows.


### Storage
The system supports local and cloud (S3) storage (via [obstore](https://developmentseed.org/obstore/latest/)).


### Processing

Processing is supported via:

- **Dask:** For scalable, parallel geospatial computation and data processing.
- **Argo Workflows:** For orchestrating complex, reproducible pipelines in Kubernetes environments. Integration is provided via the `csdr-flux` repository (not yet open-source).

## Quickstart

### Installation

The first step is to get the app running. The app repo is not yet available publicly.

#### Setting up the app
1. Clone, install. and run the app
2. Create an API key here http://localhost:3000/console/me/api-keys or in the deployed website. Leave expiry blank so it doesn't expire. Use that for the CSDR_API_KEY env var.

#### Environment Variables
3. Add env vars. Use your AWS credentials which you can find at a URL like https://{your-aws}.awsapps.com/start/
```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
export AWS_DEFAULT_REGION=ap-southeast-2
export CSDR_API_HOSTNAME=http://localhost:4000
export CSDR_API_KEY=...
# Or:
export CSDR_API_HOSTNAME=https://csdr.dev.oceandevelopmentdata.org
export CSDR_API_KEY=...
```

#### Clone and install this toolkit

4. Ensure GDAL is installed locally https://gdal.org/en/stable/download.html

5. Clone the repository and install dependencies. Dependencies are defined in [pyproject.toml](pyproject.toml)

```bash
git clone https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial.git
cd csdr-cloud-spatial
curl -Ls https://astral.sh/uv/install.sh | sh
python3 -m venv .venv
source .venv/bin/activate

uv pip install --editable .
brew install tippecanoe
```

For development, install with this command:
```bash
pip install -e ".[dev]"
```

To update the .venv and uv.lock files run `uv sync`.

#### Install using Poetry

If you prefer using Poetry for dependency management:

```bash
# Install Poetry if you don't have it already
pip install poetry

# Install dependencies
poetry install
```


#### Run premade commands

5. Run a basic geometry cache command (see [Makefile](Makefile) for more):

```bash
make geometry-eez-cache-local
```
or make your own command:
```bash
csdr <subcommand> ...
python -m csdr.cli  eez cache \
  --target-location ./cache/eez-v4/0-0-1/raw \
  --overwrite
```

### Debugging

Debug any command by adding this to a line in Python before running:
```python
import pdb; pdb.set_trace()
```

### Testing
```bash
  source .venv/bin/activate
  # uv pip install --editable .
  uv pip install --editable '.[dev]' # for the dev dependencies too
  pytest # simple as that to test all.
  pytest -s tests/test_io.py # for a specific test file. Also print output (-s).
  pytest --pdb tests/test_io.py # This opens a debugger on error.
  pytest --pdb -k test_intersection_raster -s tests/test_product.py # This tests only a specific function in a specific file.
```

### Run containerised commands

To test the code containerised (this is helpful for debugging if issues arise in Argo for example).

To build the Docker image locally using [Buildx](https://docs.docker.com/buildx/working-with-buildx/), run:

```bash
docker buildx build . --tag csdr-cloud-spatial:latest
```

Once built, you can run the container:

```bash
docker run -it --rm csdr-cloud-spatial:latest
```

#### Run the image (for python):

```bash
docker run --rm -it \
  -e AWS_ACCESS_KEY_ID="" \
  -e AWS_SECRET_ACCESS_KEY="/" \
  -e AWS_SESSION_TOKEN="" \
  -e AWS_DEFAULT_REGION=ap-southeast-2 \
  -e CSDR_API_HOSTNAME="http://localhost:4000" \
  -e CSDR_API_KEY="" \
  csdr-cloud-spatial:latest python test_script.py`
```

When changing test_script.py, you then need to rebuild the docker image before running again.

#### Run the image (for make command):

```bash
docker run --rm -it \
  -e AWS_ACCESS_KEY_ID="" \
  -e AWS_SECRET_ACCESS_KEY="/" \
  -e AWS_SESSION_TOKEN="" \
  -e AWS_DEFAULT_REGION=ap-southeast-2 \
  -e CSDR_API_HOSTNAME="http://localhost:4000" \
  -e CSDR_API_KEY="" \
  csdr-cloud-spatial:latest sh -c "make product-gmw-v4-eez-process-geometry-s3"
```
#### Skip steps by copying earlier outputs to container

Run commands locally, then copy to Docker container using `docker cp <container_id>:/code/cache ./cache`, then when running the docker image, this cache is copied to the container so that they are ready to test dependent steps in a workflow.



## CI/CD

Github Actions Lint, Test, Build, and Push Docker Image to to Amazon ECR. This happens in two scenarios:

- **Main Branch:** A merge to `main` automatically triggers the workflow. The image is tagged with a version (based on git tags) and additionally gets a `latest` tag.

- **Manual Trigger:** Developers can manually trigger the workflow (using `workflow_dispatch`) to create and push a test build from their feature branch without the `latest` tag.


## Contributing

Contributions are welcome! Please open issues or pull requests for bug fixes, new features, or documentation improvements.

To contribute:
- Fork the repository and create a feature branch.
- Follow the code style and pre-commit hooks (`pre-commit install`).
- Add or update tests as appropriate.
- Open a pull request with a clear description of your changes.


#### Pre commit hooks

Formats Python, YAML, and JSON.

To use pre-commit to automatically run ruff, mypy and other checks on each commit, make sure the development dependencies are installed and then run:

```bash
pre-commit install
```

Note that you will need to run `pre-commit run --all-files` if any of the hooks in `.pre-commit-config.yaml` change.


## Details of a single workflow

How to run a whole EEZ Geometries workflow. This was documented to develop the Argo Workflow template for this geometry.

1. Manual step. Create geometry (writes to db.geometry table). Do this here: http://localhost:3000/console/geometries or in the deployed app. Leave ID blank to be autogenerated. Name is the only needed field.
2. CLI Command. Run cache command (writes zipped shapefile to S3) `make geometry-eez-cache-s3`.
3. CLI Command. Convert command (writes zipped shapefile's data as parquet and PMTiles to S3). Replace run ID with the id . `make geometry-eez-convert-s3`.
4. CLI Command. Provenance (writes provenance and geometries to S3 and to db.geometries_run and geometries_outputs tables). `make geometry-eez-provenance-s3-db`.
5. Manual step: in the app, see the new geometry run. Click on it and then click the button to make this run the main run.


### Outputs:

Running the EEZ workflow creates or overwrites these files, and inserts records to these DB tables (no update in DB):

#### Files:

- `Bucket/geometries/eez-v4/0-0-1/raw/{file_name}.zip` (note no run_id)
- `Bucket/geometries/eez-v4/0-0-1/runs/{run_id}/{file_name}.parquet`
- `Bucket/geometries/eez-v4/0-0-1/runs/{run_id}/{file_name}.pmtiles`
- `Bucket/geometries/eez-v4/0-0-1/runs/{run_id}/{file_name}.parquet.provenance.json`

#### DB tables:

- `geometries`. This is what you created in the app.
- `geometries_run`. This is what the provenance command created.
- `geometry_output`. This is a record for each geometry in a run. For example one EEZ geometry output is Australia.
