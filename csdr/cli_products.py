import json
import sys

import typer
from loguru import logger
from obstore.store import S3Store

from csdr.io import (
    get_prefix,
    get_store_for_url,
    get_url_from_store_filename,
    read_geospatial_file,
    write_json,
)
from csdr.products import process_variables_for_geometry
from csdr.provenance import read_provenance
from csdr.utils import get_geom_from_gdf

products_app = typer.Typer()


KNOWN_VARIABLES = ["sum-area-by-value"]


@products_app.command("list-geometries")
def list_geometries(
    geometry_provenance_url: str = typer.Option(
        ..., help="URL that points to the geometry provenance file"
    ),
    out_file: str = typer.Option(
        None, help="Tempfile to write list of IDs to (otherwise print to console)"
    ),
) -> None:
    logger.info(f"Dumping list of geometry ids for {geometry_provenance_url}")

    # Load the provenance file
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataset_url")

    logger.info(f"Reading geometries from {geometry_file_url}")
    gdf = read_geospatial_file(geometry_file_url)
    logger.info(f"Found {len(gdf)} geometries")

    ids_list = gdf.index.to_list()

    if out_file is not None:
        with open(out_file, "w") as f:
            json.dump(ids_list, f, indent=4)
        logger.info(f"Wrote geometry ids to {out_file}")
    else:
        sys.stdout.write(json.dumps(ids_list, indent=4))

    logger.info(provenance)


@products_app.command("process-geometry")
def process_geometry(
    geometry_provenance_url: str = typer.Option(
        ..., help="URL that points to the geometry provenance file"
    ),
    dataset_provenance_url: str = typer.Option(
        None, help="URL that points to the dataset provenance file"
    ),
    variables_to_extract: str = typer.Option(
        "sum-area-by-value",
        help="Comma-separated list of variables to extract from the dataset",
        parser=lambda s: s.split(","),
    ),
    product_name: str = typer.Option(
        "example-product", help="Name of the product being generated"
    ),
    target_location: str = typer.Option(
        "cache/products",
        help="Location to write the results to (otherwise print to console)",
    ),
    geometry_id: str = typer.Option(..., help="ID of the geometry to process"),
) -> None:
    logger.info(f"Processing geometry {geometry_id} from {geometry_provenance_url}")

    if set(variables_to_extract) - set(KNOWN_VARIABLES):
        logger.error(
            f"Unknown variable to extract: {variables_to_extract}. Known variables are: {KNOWN_VARIABLES}"
        )
        raise typer.Exit(code=1)

    # Load the provenance file
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataset_url")
    logger.info(f"Reading geometries from {geometry_file_url}")

    gdf = read_geospatial_file(geometry_file_url)

    # if geometry_id not in gdf.index:
    #     logger.error(f"Geometry ID {geometry_id} not found in dataset")
    #     raise typer.Exit(code=1)

    geometry = get_geom_from_gdf(gdf, geometry_id)
    results = process_variables_for_geometry(
        geometry, variables_to_extract, dataset_provenance_url
    )

    logger.info(f"Results for geometry {geometry_id}: {results}")

    # Write results out
    dest = get_store_for_url(target_location)
    path = f"{product_name}/test-var/{product_name}-{geometry_id}.json"

    if type(dest) is S3Store:
        prefix = get_prefix(target_location)
        if prefix is not None:
            path = f"{prefix}/{path}"

    write_json(dest, path, results)

    logger.info(f"Wrote results to {get_url_from_store_filename(dest, path)}")
