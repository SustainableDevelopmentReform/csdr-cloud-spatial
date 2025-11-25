import typer

from csdr import get_version
from csdr.cli_conversion import conversion_app
from csdr.cli_dataset_gmw import gmw_app
from csdr.cli_dataset_seagrass import seagrass_app
from csdr.cli_datasets import dataset_app
from csdr.cli_dvc import dvc_app
from csdr.cli_geometries import geometry_app
from csdr.cli_geometry_eez import eez_app
from csdr.cli_helpers import helpers_app
from csdr.cli_products import products_app
from csdr.cli_provenance import provenance_app
from csdr.cli_vector_cube import vector_cube_app

app = typer.Typer()

# Add the subcommands
app.add_typer(dataset_app, name="datasets", help="Commands for processing datasets.")
app.add_typer(
    geometry_app, name="geometries", help="Commands for processing geometries."
)
app.add_typer(
    vector_cube_app,
    name="vector-cube",
    help="Commands for vector-cube operations like zonal statistics.",
)
app.add_typer(dvc_app, name="dvc", help="Commands for DVC operations.")

## Datasets
# GMW
app.add_typer(gmw_app, name="gmw", help="Cache and process the GMW dataset.")
# Seagrass, including from DEP
app.add_typer(
    seagrass_app, name="seagrass", help="Cache and process Seagrass datasets."
)

# EEZ
app.add_typer(eez_app, name="eez", help="Cache and process the EEZ dataset.")

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
