import json
import sys
from typing import Any

import typer
from dask.distributed import Client
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
from csdr.utils import get_geom_from_gdf, make_uuid

products_app = typer.Typer()


KNOWN_VARIABLES = ["sum-area-by-value"]


def opt_dict_parser(s: str | dict[str, Any]) -> dict[str, Any]:
    if type(s) is dict:
        return s

    result = dict(item.split("=", 1) for item in s.split(",") if "=" in item)

    # Try parsing values to int or float
    for key, value in result.items():
        try:
            result[key] = int(value)
        except ValueError:
            try:
                result[key] = float(value)
            except ValueError:
                pass

    return result


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

    ids_list = gdf["csdr-id"].tolist()

    if out_file is not None:
        with open(out_file, "w") as f:
            json.dump(ids_list, f, indent=4)
        logger.info(f"Wrote geometry ids to {out_file}")
    else:
        sys.stdout.write(json.dumps(ids_list, indent=4))


@products_app.command("process-geometry")
def process_geometry(
    product_id: str = typer.Option(
        "example-product", help="ID of the product being generated"
    ),
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
    datetime_string_match: str = typer.Option(
        None,
        help="If set, only process data from items whose datetime string contains this value (e.g. '2024' to get all items from 2024)",
    ),
    target_location: str = typer.Option(
        "cache/products",
        help="Location to write the results to (otherwise print to console)",
    ),
    geometry_id: str = typer.Option(..., help="ID of the geometry to process"),
    variable_name: str = typer.Option(
        "asset", help="Name of the variable to use for calculations (if applicable)"
    ),
    variable_value: str | None = typer.Option(
        None, help="Value of the variable to use for calculations (if applicable)"
    ),
    load_kwargs: dict[str, str] = typer.Option(
        {},
        "--load-kwargs",
        help="Options to pass to xarray load function, in the form --load-kwargs key1=value1,key2=value2",
        parser=opt_dict_parser,
    ),
    use_dask: bool = typer.Option(
        False, help="Whether to use Dask distributed for processing"
    ),
    dask_client_opts: dict[str, str] = typer.Option(
        {},
        "--dask-opts",
        help="Options to pass to Dask client, in the form --dask-opts key1=value1,key2=value2",
        parser=opt_dict_parser,
    ),
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

    # Use Dask distributed here
    if use_dask:
        logger.info(f"Starting Dask client with options: {dask_client_opts}")

        # Parse the client opts, making them integers if they can be
        for key, value in dask_client_opts.items():
            try:
                dask_client_opts[key] = int(value)
            except ValueError:
                pass

        client = Client(**dask_client_opts)
        logger.info(
            f"Dask client started: {client.dashboard_link if hasattr(client, 'dashboard_link') else client}"
        )

    results = process_variables_for_geometry(
        geometry,
        variables_to_extract,
        dataset_provenance_url,
        datetime_string_match=datetime_string_match,
        variable_name=variable_name,
        variable_value=variable_value,
        load_kwargs=load_kwargs,
    )

    if use_dask:
        client.close()

    logger.info(f"Results for geometry {geometry_id}: {results}")

    # TODO: Validate product id, variable name and other things.

    # Write results out
    dest = get_store_for_url(target_location)
    path = f"{product_id}/{variable_name}/{product_id}-{geometry_id}.json"

    if type(dest) is S3Store:
        prefix = get_prefix(target_location)
        if prefix is not None:
            path = f"{prefix}/{path}"

    geometry_output_id = geometry_id

    product_output = {
        "id": make_uuid(
            f"{product_id}-{geometry_id}-{geometry_provenance_url}-{dataset_provenance_url}"
        ),
        "product_id": product_id,
        "geometry_id": geometry_output_id,
        "variables": results,
        "geometry_provenance_url": geometry_provenance_url,
        "dataset_provenance_url": dataset_provenance_url,
    }

    write_json(dest, path, product_output)

    logger.info(f"Wrote results to {get_url_from_store_filename(dest, path)}")
