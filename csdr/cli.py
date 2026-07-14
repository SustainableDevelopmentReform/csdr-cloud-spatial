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
from csdr.cli_dataset_stac import stac_app
from csdr.cli_geometry_acsc2 import acsc2_app
from csdr.cli_geometry_aus_states import aus_states_app
from csdr.cli_geometry_cwa import cwa_app
from csdr.cli_geometry_eez import eez_app
from csdr.cli_helpers import helpers_app
from csdr.cli_products import products_app
from csdr.cli_provenance import provenance_app

app = typer.Typer()

# All files will inherit this logging configuration so we only write once
logging.basicConfig(
    level=logging.WARNING,  # Package logging level.
    format="%(asctime)s | %(levelname)s | %(module)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
    force=True,
)
logging.getLogger("csdr").setLevel(logging.INFO)  # Our logging level.

# Add the subcommands

## Datasets
# GMW
app.add_typer(gmw_app, name="gmw", help="Cache and process the GMW datasets.")
# Seagrass, including from DEP
app.add_typer(
    seagrass_app, name="seagrass", help="Cache and process Seagrass datasets."
)
# Generic static STAC item indexer (e.g. Global Seagrass COG mosaics)
app.add_typer(
    stac_app,
    name="stac",
    help="Index static STAC items (COGs) into a single STAC-Geoparquet.",
)
# ACA
app.add_typer(aca_app, name="aca", help="Cache and process ACA dataset.")
# ACE
app.add_typer(ace_app, name="ace", help="Cache and process ACE dataset.")
# MS Buildings
app.add_typer(
    buildings_app, name="buildings", help="Cache and process buildings dataset."
)

## Geometries
# ACSC2
app.add_typer(
    acsc2_app,
    name="acsc2",
    help="Cache and process the Australian Coastal Sediment Compartments - Secondary Compartments dataset.",
)
# CWA
app.add_typer(
    cwa_app, name="cwa", help="Cache and process the GA Coastal Waters Areas dataset."
)
# EEZ
app.add_typer(eez_app, name="eez", help="Cache and process the EEZ dataset.")
# ABS Australian States
app.add_typer(
    aus_states_app,
    name="aus-states",
    help="Cache and process the ABS Australian States dataset.",
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
