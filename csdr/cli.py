import logging
import sys

import typer

from csdr import get_version
from csdr.cli_conversion import conversion_app
from csdr.cli_dataset_aca import aca_app
from csdr.cli_dataset_ace import ace_app
from csdr.cli_dataset_buildings import buildings_app
from csdr.cli_dataset_gmw import gmw_app
from csdr.cli_dataset_seagrass import seagrass_app
from csdr.cli_geometries import geometry_app
from csdr.cli_helpers import helpers_app
from csdr.cli_products import products_app
from csdr.cli_provenance import provenance_app

app = typer.Typer()

# All files will inherit this logging configuration so we only write once
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(module)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)

## Datasets
# GMW
app.add_typer(gmw_app, name="gmw", help="Cache and process the GMW datasets.")
# Seagrass, including from DEP
app.add_typer(
    seagrass_app, name="seagrass", help="Cache and process Seagrass datasets."
)
# ACA
app.add_typer(aca_app, name="aca", help="Cache and process ACA dataset.")
# ACE
app.add_typer(ace_app, name="ace", help="Cache and process ACE dataset.")
# MS Buildings
app.add_typer(
    buildings_app, name="buildings", help="Cache and process buildings dataset."
)

## Geometries - for all geometries e.g. CWA, EEZ, ACSC2, Aus States, etc.
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
