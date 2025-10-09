import json
import sys
from typing import Any

import dateutil
import pandas as pd
import typer
from dask.distributed import Client
from loguru import logger
from obstore.store import S3Store

from csdr.io import (
    exists,
    get_prefix,
    get_store_for_url,
    get_url_from_store_filename,
    read_dict,
    read_geospatial_file,
    write_gdf_to_parquet,
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


def version_parser(s: str) -> str:
    return s.lstrip("v").replace(".", "-")


def get_product_path(
    product_id: str,
    version: str,
    variable_name: str,
    geometry_id: str | None = None,
) -> str:
    path = f"{product_id}/{variable_name}/{version}"
    if geometry_id is not None:
        path = f"{path}/{product_id}-{geometry_id}.json"
    return path


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
    geometry_file_url = provenance.get("dataUrl")

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
    version: str = typer.Option(
        "0.0.0",
        help="Semver-like version of the product being generated. Todo: workout how to replace with product-run-id",
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
    datetime: str | None = typer.Option(
        None,
        help="Parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01')",
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
    overwrite: bool = typer.Option(
        False, help="If true, overwrite existing product file"
    ),
) -> None:
    logger.info(f"Processing geometry {geometry_id} from {geometry_provenance_url}")

    variable_value = float(variable_value) if variable_value is not None else None

    if set(variables_to_extract) - set(KNOWN_VARIABLES):
        logger.error(
            f"Unknown variable to extract: {variables_to_extract}. Known variables are: {KNOWN_VARIABLES}"
        )
        raise typer.Exit(code=1)

    if datetime is None:
        if datetime_string_match is None:
            logger.error(
                "Either datetime or datetime_string_match must be set to process a geometry"
            )
            raise typer.Exit(code=1)
        else:
            datetime = datetime_string_match

    version_clean = version_parser(version)

    # Get paths for writing results
    dest = get_store_for_url(target_location)
    path = get_product_path(
        product_id,
        version_clean,
        variable_name,
        geometry_id=geometry_id,
    )

    if type(dest) is S3Store:
        prefix = get_prefix(target_location)
        if prefix is not None:
            path = f"{prefix}/{path}"

    dest_url = get_url_from_store_filename(dest, path)
    logger.info(f"Will write results to {dest_url}")

    if exists(dest, path) and not overwrite:
        logger.info(f"Product already exists at {dest_url}, skipping processing.")
        raise typer.Exit(code=0)  # Exit successfully, nothing to do

    # Load the provenance file
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataUrl")
    logger.info(f"Reading geometries from {geometry_file_url}")

    gdf = read_geospatial_file(geometry_file_url)

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
    print(results)

    if use_dask:
        client.close()

    logger.info(f"Results for geometry {geometry_id}: {results}")

    # TODO: Validate product id, variable name and other things.

    product_output = {
        "id": make_uuid(
            f"{product_id}-{geometry_id}-{geometry_provenance_url}-{dataset_provenance_url}"
        ),
        "productId": product_id,
        "geometryOutputId": geometry_id,
        "timePoint": dateutil.parser.isoparse(datetime).isoformat() + "Z",
        "variables": results,
        "metadata": {
            "geometryProvenanceUrl": geometry_provenance_url,
            "datasetProvenanceUrl": dataset_provenance_url,
        },
    }

    write_json(dest, path, product_output)

    logger.info(f"Wrote results to {get_url_from_store_filename(dest, path)}")


@products_app.command("process-all-geometries")
def process_all_geometries(
    product_id: str = typer.Option(
        "example-product", help="ID of the product being generated"
    ),
    version: str = typer.Option(
        "0.0.0",
        help="Semver-like version of the product being generated. Todo: workout how to replace with product-run-id",
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
    datetime: str | None = typer.Option(
        None,
        help="Parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01')",
    ),
    target_location: str = typer.Option(
        "cache/products",
        help="Location to write the results to (otherwise print to console)",
    ),
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
    overwrite: bool = typer.Option(
        False, help="If true, overwrite existing product file"
    ),
) -> None:
    logger.info(
        f"Processing all geometries from {geometry_provenance_url} for product {product_id}"
    )

    variable_value = float(variable_value) if variable_value is not None else None

    if set(variables_to_extract) - set(KNOWN_VARIABLES):
        logger.error(
            f"Unknown variable to extract: {variables_to_extract}. Known variables are: {KNOWN_VARIABLES}"
        )
        raise typer.Exit(code=1)

    if datetime is None:
        if datetime_string_match is None:
            logger.error(
                "Either datetime or datetime_string_match must be set to process a geometry"
            )
            raise typer.Exit(code=1)
        else:
            datetime = datetime_string_match

    version_clean = version_parser(version)

    # Get paths for writing results
    dest = get_store_for_url(target_location)
    base_path = get_product_path(product_id, version_clean, variable_name)

    if type(dest) is S3Store:
        prefix = get_prefix(target_location)
        if prefix is not None:
            base_path = f"{prefix}/{base_path}"

    dest_url = get_url_from_store_filename(dest, base_path)
    logger.info(f"Will write results to {dest_url}")

    # Load the provenance file
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataUrl")
    logger.info(f"Reading geometries from {geometry_file_url}")

    gdf = read_geospatial_file(geometry_file_url)
    logger.info(f"Found {len(gdf)} geometries")

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

    for _, row in gdf.iterrows():
        geometry_id = row["csdr-id"]
        geometry = get_geom_from_gdf(gdf, geometry_id)

        path = f"{base_path}/{product_id}-{geometry_id}.json"

        if exists(dest, path) and not overwrite:
            logger.info(
                f"Product already exists at {get_url_from_store_filename(dest, path)}, skipping processing for geometry {geometry_id}."
            )
            continue  # Nothing to do
        logger.info(f"Processing geometry {geometry_id}")
        results = process_variables_for_geometry(
            geometry,
            variables_to_extract,
            dataset_provenance_url,
            datetime_string_match=datetime_string_match,
            variable_name=variable_name,
            variable_value=variable_value,
            load_kwargs=load_kwargs,
        )
        logger.info(f"Results for geometry {geometry_id}: {results}")

        product_output = {
            "id": make_uuid(
                f"{product_id}-{geometry_id}-{geometry_provenance_url}-{dataset_provenance_url}"
            ),
            "productId": product_id,
            "geometryOutputId": geometry_id,
            "timePoint": dateutil.parser.isoparse(datetime).isoformat() + "Z",
            "variables": results,
            "metadata": {
                "geometryProvenanceUrl": geometry_provenance_url,
                "datasetProvenanceUrl": dataset_provenance_url,
            },
        }
        write_json(dest, path, product_output)
    if use_dask:
        client.close()

    logger.info(f"Wrote results to {dest_url}")


@products_app.command("consolidate")
def consolidate_product(
    product_id: str = typer.Option(
        "example-product", help="ID of the product being consolidated"
    ),
    version: str = typer.Option(
        "0.0.0",
        help="Semver-like version of the product being consolidated. Todo: workout how to replace with product-run-id",
    ),
    location: str = typer.Option(
        "cache/products", help="Location to read the product files from"
    ),
    geometry_provenance_url: str = typer.Option(
        ..., help="URL that points to the geometry provenance file"
    ),
    dataset_provenance_url: str = typer.Option(
        None, help="URL that points to the dataset provenance file"
    ),
    variable_name: str = typer.Option(
        "asset", help="Name of the variable to use for calculations (if applicable)"
    ),
) -> None:
    logger.info(f"Consolidating product {product_id} from {location}")

    store = get_store_for_url(location)

    version_clean = version_parser(version)
    path = get_product_path(product_id, version_clean, variable_name)

    if type(store) is S3Store:
        prefix = get_prefix(location)
        if prefix is not None:
            path = f"{prefix}/{path}"

    url = get_url_from_store_filename(store, path)
    logger.info(f"Looking for product files in {url}")

    # Get a list of all the json files in the product directory
    json_files = [f for f in store.list(path)]
    json_files = [f["path"] for f in json_files[0] if f["path"].endswith(".json")]

    logger.info(f"Found {len(json_files)} product files to consolidate")

    # Load each file and combine into a pandas DataFrame
    all_data = []
    for file in json_files:
        logger.info(f"Loading product file {file}")
        product = read_dict(store, file)
        geometry_provenance_url = product.get("metadata", {}).get(
            "geometryProvenanceUrl", None
        )
        dataset_provenance_url = product.get("metadata", {}).get(
            "datasetProvenanceUrl", None
        )

        # Do some quality checks
        if dataset_provenance_url != dataset_provenance_url:
            raise ValueError(
                f"Dataset provenance URL mismatch in {file}: {dataset_provenance_url} != {dataset_provenance_url}"
            )
        if product_id != product.get("productId", None):
            raise ValueError(
                f"Product ID mismatch in {file}: {product_id} != {product.get('productId', None)}"
            )
        if geometry_provenance_url != geometry_provenance_url:
            raise ValueError(
                f"Geometry provenance URL mismatch in {file}: {geometry_provenance_url} != {geometry_provenance_url}"
            )

        all_data.append(product)

    if not all_data:
        logger.error(f"No valid product data found in {url}")
        raise typer.Exit(code=1)

    df = pd.DataFrame(all_data)
    logger.info(f"Consolidated product data from {url}: {df.shape[0]} rows")

    # Write the consolidated DataFrame to a new parquet
    output_file = f"{path}/{product_id}-{version_clean}.parquet"
    write_gdf_to_parquet(df, store, output_file)

    out_url = get_url_from_store_filename(store, output_file)
    logger.info(f"Wrote consolidated product data to {out_url}")
