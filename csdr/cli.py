import logging
import sys

import typer

from csdr import get_version
from csdr.cli_conversion import conversion_app
from csdr.cli_dataset_partition_parquets import dataset_partition_parquets_app
from csdr.cli_dataset_stac import dataset_stac_app
from csdr.cli_dataset_zip_cogs import dataset_zip_cogs_app
from csdr.cli_dataset_zip_shp import dataset_zip_shp_app
from csdr.cli_datasets import dataset_app
from csdr.cli_dvc import dvc_app
from csdr.cli_geometries import geometry_app
from csdr.cli_helpers import helpers_app
from csdr.cli_products import products_app
from csdr.cli_provenance import provenance_app
from csdr.cli_vector_cube import vector_cube_app

app = typer.Typer()

# All files will inherit this logging configuration so we only write once
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)

# Add the subcommands
app.add_typer(dataset_app, name="datasets", help="Commands for processing datasets.")
app.add_typer(
    vector_cube_app,
    name="vector-cube",
    help="Commands for vector-cube operations like zonal statistics.",
)
app.add_typer(dvc_app, name="dvc", help="Commands for DVC operations.")

## Datasets
# TODO: Replace these dataset CLIs with generic 4 categories:
# 1. STAC API - DEP Seagrass, ACE.
# 2. Zipped COGs - GMW v3 and GMW v4.
# 3. Partitioned Parquets - VIDA Buildings.
# 4. Zipped Shapefile - ACA Reef.
app.add_typer(dataset_stac_app, name="dataset-stac", help="Process STAC datasets.")
app.add_typer(
    dataset_zip_cogs_app, name="dataset-zip-cogs", help="Process Zipped COGs datasets."
)
app.add_typer(
    dataset_partition_parquets_app,
    name="dataset-partition-parquets",
    help="Process Partitioned Parquets datasets.",
)
app.add_typer(
    dataset_zip_shp_app,
    name="dataset-zip-shp",
    help="Process Zipped Shapefile datasets.",
)


## Geometries
app.add_typer(
    geometry_app, name="geometries", help="Commands for processing geometries."
)

# Generic conversion tools
app.add_typer(conversion_app, name="convert", help="Data conversion tools.")

# Provenance and metadata
app.add_typer(provenance_app, name="provenance", help="Provenance tools.")

# Products
app.add_typer(products_app, name="products", help="Product generation tools.")

# Helpers
app.add_typer(helpers_app, name="helpers", help="Helper commands.")


# Work for version and --version
@app.command()
def version() -> None:
    """Echo the version of the software."""

    version = get_version()
    typer.echo(version)

    return


if __name__ == "__main__":
    app()
