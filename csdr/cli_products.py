import os
import json
import sys
from typing import Any

import dateutil
import pandas as pd
import typer
from dask.distributed import Client
from loguru import logger
from obstore.store import HTTPStore, LocalStore, S3Store
from odc.geo.geom import Geometry
import asyncio

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


def _validate_parameters(
    variables_to_extract: list[str],
    datetime: str | None,
    datetime_string_match: str | None,
) -> str:
    """Validate parameters and return the datetime to use."""
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

    return datetime


def _setup_dask_client(
    use_dask: bool, dask_client_opts: dict[str, Any]
) -> Client | None:
    """Set up Dask client if requested."""
    if not use_dask:
        logger.info("Use Dask parameter is False. Not starting Dask client.")
        return None

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
    return client


def _load_geometry_data(geometry_provenance_url: str) -> tuple[Any, str]:
    """Load geometry provenance and return gdf and geometry_file_url."""
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataUrl")
    logger.info(f"Reading geometries from {geometry_file_url}")
    gdf = read_geospatial_file(geometry_file_url)
    return gdf, geometry_file_url


def _create_product_output(
    product_id: str,
    geometry_id: str,
    geometry_provenance_url: str,
    dataset_provenance_url: str,
    datetime: str,
    results: dict[str, Any],
    run_id: str
) -> dict[str, Any]:
    """Create the product output dictionary."""
    return {
        "id": make_uuid(
            f"{product_id}-{geometry_id}-{geometry_provenance_url}-{dataset_provenance_url}"
        ),
        "productId": product_id,
        "productRunId": run_id,
        "geometryOutputId": geometry_id,
        "timePoint": dateutil.parser.isoparse(datetime).isoformat() + "Z",
        "variables": results,
        "metadata": {
            "geometryProvenanceUrl": geometry_provenance_url,
            "datasetProvenanceUrl": dataset_provenance_url,
        },
    }

# # Is _process_single_geometry the planned way to do it? While process_geometry is for debugging?
# # Uses external Dask client passed from process_all_geometries I think.
# # I guess I need to update process_single_geometry because I have updated process_geometry.
# def _process_single_geometry(
#     geometry_id: str,
#     geometry: Geometry,
#     run_id: str,
#     variables_to_extract: list[str],
#     dataset_provenance_url: str,
#     datetime_string_match: str | None,
#     variable_name: str,
#     variable_value: float | None,
#     load_kwargs: dict[str, Any],
#     product_id: str,
#     geometry_provenance_url: str,
#     datetime: str,
#     target_store: HTTPStore | LocalStore | S3Store,
#     path: str,
#     overwrite: bool,
# ) -> bool:
#     """Process a single geometry and return True if processed, False if skipped."""
#     if exists(target_store, path) and not overwrite:
#         logger.info(
#             f"Product already exists at {get_url_from_store_filename(target_store, path)}, skipping processing for geometry {geometry_id}."
#         )
#         return False

#     logger.info(f"Processing geometry {geometry_id}")
#     results = process_variables_for_geometry(
#         geometry,
#         variables_to_extract,
#         dataset_provenance_url,
#         datetime_string_match=datetime_string_match,
#         variable_name=variable_name,
#         variable_value=variable_value,
#         load_kwargs=load_kwargs,
#     )
#     logger.info(f"Results for geometry {geometry_id}: {results}")

#     product_output = _create_product_output(
#         product_id,
#         geometry_id,
#         geometry_provenance_url,
#         dataset_provenance_url,
#         datetime,
#         results,
#         run_id,
#     )
#     write_json(target_store, path, product_output)
#     return True

def parse_csv_list(value: str) -> list[str]:
    return value.split(",") if value else []

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
    variable_name: str,
    run_id: str,
    datetime: str | None = None, # This is not the current datetime but rather the parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01'). Parameter could be more explicitly named.
    geometry_id: str | None = None,
) -> str:
    path = f"runs/{run_id}/{variable_name}"
    if datetime is not None:
        path = f"{path}/{datetime}"
    # geometry id is just for the processing of single geometries
    if geometry_id is not None:
        path = f"{path}/{product_id}-{geometry_id}.json" # Is product_id needed in filename because the path includes the run-id which is more specific.
    return path


@products_app.command("list-geometries")
def list_geometries(
    geometry_provenance_url: str = typer.Option(
        ..., help="URL that points to the geometry provenance file"
    ),
    # Should out_file be optional? Should it then default to printing to console?
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
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        with open(out_file, "w") as f:
            json.dump(ids_list, f, indent=4)
        logger.info(f"Wrote geometry ids to {out_file}")
    else:
        sys.stdout.write(json.dumps(ids_list, indent=4))

# Process a single geometry. Makes variables using variables_to_extract. Writes the results to a json file.
@products_app.command("process-geometry")
def process_geometry_sync(
    product_id: str = typer.Option(
        "example-product", help="ID of the product being generated (UUID)"
    ),
    run_id: str = typer.Option(
        ..., help="ID of the product run"
    ),
    geometry_provenance_url: str = typer.Option(
        ..., help="URL that points to the geometry provenance file"
    ),
    dataset_provenance_url: str = typer.Option(
        ..., help="URL that points to the dataset provenance file"
    ),
    variables_to_extract: str = typer.Option( # This type needs to be str to accept the param but then it is incorrectly str instead of list[str] in the function
        "sum-area-by-value",
        help="Comma-separated list of variables to extract from the dataset",
        parser=parse_csv_list
    ),
    datetime_string_match: str | None = typer.Option(
        None,
        help="If set, only process data from items whose datetime string contains this value (e.g. '2024' to get all items from 2024)",
    ),
    datetime: str | None = typer.Option(
        None,
        help="Parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01')",
    ),
    target_location: str = typer.Option(
        "./cache/products/<product>/0-0-1",
        help="Location to write the JSON result to",
    ),
    geometry_id: str = typer.Option(..., help="ID of the geometry to process."), # This is the actual single geometry being processed. Not to be confused with the EEZ Geometry. This is one geometry within that i.e. one EEZ e.g. Fiji.
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
    asyncio.run(process_geometry(
        product_id=product_id,
        run_id=run_id,
        geometry_provenance_url=geometry_provenance_url,
        dataset_provenance_url=dataset_provenance_url,
        variables_to_extract=variables_to_extract,
        datetime_string_match=datetime_string_match,
        datetime=datetime,
        target_location=target_location,
        geometry_id=geometry_id,
        variable_name=variable_name,
        variable_value=variable_value,
        load_kwargs=load_kwargs,
        use_dask=use_dask,
        dask_client_opts=dask_client_opts,
        overwrite=overwrite
    ))

async def process_geometry(
    product_id: str,
    run_id: str,
    geometry_provenance_url: str,
    dataset_provenance_url: str,
    variables_to_extract: str,
    datetime_string_match: str | None,
    datetime: str | None,
    target_location: str,
    geometry_id: str,
    variable_name: str,
    variable_value: str | None,
    load_kwargs: dict[str, str],
    use_dask: bool,
    dask_client_opts: dict[str, str],
    overwrite: bool
) -> None: # update this to return true if processed, false if skipped?
    # This logic is the same as in process_all_geometries, but just does one geometry.
    logger.info(f"Processing geometry '{geometry_id}' from '{geometry_provenance_url}'")
    logger.info(f"Run ID: '{run_id}'")
    logger.info(f"variables_to_extract {variables_to_extract}") # TODO: change type of variables_to_extract from str to list[str]

    target_location = target_location.rstrip("/") # Remove trailing slash if present

    # Validate parameters
    variable_value_float = float(variable_value) if variable_value is not None else None
    datetime = _validate_parameters(
        variables_to_extract, datetime, datetime_string_match
    )

    # Get paths for writing result JSON
    target_store = get_store_for_url(target_location)
    # this path includes the filename (because geometry_id is provided to get_product_path)
    target_path = get_product_path(
        product_id,
        variable_name,
        run_id=run_id,
        geometry_id=geometry_id,
        datetime=datetime,
    )

    # TODO: refactor writing path/file code into a function. Same logic in cli_geometry_eez.py

    # TODO: make this S3 prefix code a function.
    if type(target_store) is S3Store:
        # S3Store needs the full path including prefix
        prefix = get_prefix(target_location)
        if prefix is not None:
            target_path = f"{prefix}/{target_path}"
    target_url = get_url_from_store_filename(target_store, target_path)
    logger.info(f"target_url: {target_url}")

    if exists(target_store, target_path) and not overwrite:
            logger.info(f"Product already exists at {target_url}, skipping processing.")
            raise typer.Exit(code=0)  # Exit successfully, nothing to do

    logger.info(f"JSON doesn't exist for {geometry_id} or overwrite is True, processing geometry.")

    # Load geometry data
    gdf, _ = _load_geometry_data(geometry_provenance_url) # GeoDataFrame
    geometry = get_geom_from_gdf(gdf, geometry_id) # ODC Geometry object. This is just one geometry.

    # Set up Dask client
    # What is this Dask client used for? It isn't used except to be closed. Is it used indirectly in process_variables_for_geometry (load_xarray_stacgeoparquet)?
    # use_dask defaults to False
    client = _setup_dask_client(use_dask, dask_client_opts)

    try:
        results = process_variables_for_geometry(
            geometry,
            variables_to_extract,
            dataset_provenance_url,
            datetime_string_match=datetime_string_match,
            variable_name=variable_name,
            variable_value=variable_value_float,
            load_kwargs=load_kwargs,
        )
        logger.info(f"Results for geometry {geometry_id}: {results}")

        # TODO: Validate product id, variable name and other things.
        # Product ID will be created in product workflow.

        product_output = _create_product_output(
            product_id,
            geometry_id,
            geometry_provenance_url,
            dataset_provenance_url,
            datetime,
            results,
            run_id,
        )

        # write_json(target_store, target_path, product_output)
        # logger.info(f"Wrote results to {target_url}")

        # try to use async put instead of write_json. This is consistent with geometry eez code.
        logger.info(f"Writing to {target_url}. target_path: {target_path}...")
        await target_store.put_async(target_path, json.dumps(product_output).encode("utf-8"))
        logger.info(f"Wrote results to {target_url}")

    finally:
        if client is not None:
            client.close()

# # process-all-geometries is for debugging parallel processing of all geometries. It is not used in the product workflow template.
# @products_app.command("process-all-geometries")
# def process_all_geometries(
#     product_id: str = typer.Option(
#         "example-product", help="ID of the product being generated (UUID)"
#     ),
#     run_id: str = typer.Option(
#         ..., help="ID of the product run"
#     ),
#     geometry_provenance_url: str = typer.Option(
#         ..., help="URL that points to the geometry provenance file"
#     ),
#     dataset_provenance_url: str = typer.Option(
#         ..., help="URL that points to the dataset provenance file"
#     ),
#     variables_to_extract: str = typer.Option( # This type needs to be str to accept the param but then it is incorrectly str instead of list[str] in the function
#         "sum-area-by-value",
#         help="Comma-separated list of variables to extract from the dataset",
#         parser=parse_csv_list
#     ),
#     datetime_string_match: str = typer.Option(
#         None,
#         help="If set, only process data from items whose datetime string contains this value (e.g. '2024' to get all items from 2024)",
#     ),
#     datetime: str | None = typer.Option(
#         None,
#         help="Parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01')",
#     ),
#     target_location: str = typer.Option(
#         "./cache/products/<product>/0-0-1",
#         help="Location to write the results to",
#     ),
#     variable_name: str = typer.Option(
#         "asset", help="Name of the variable to use for calculations (if applicable)"
#     ),
#     variable_value: str | None = typer.Option(
#         None, help="Value of the variable to use for calculations (if applicable)"
#     ),
#     load_kwargs: dict[str, str] = typer.Option(
#         {},
#         "--load-kwargs",
#         help="Options to pass to xarray load function, in the form --load-kwargs key1=value1,key2=value2",
#         parser=opt_dict_parser,
#     ),
#     use_dask: bool = typer.Option(
#         False, help="Whether to use Dask distributed for processing"
#     ),
#     dask_client_opts: dict[str, str] = typer.Option(
#         {},
#         "--dask-opts",
#         help="Options to pass to Dask client, in the form --dask-opts key1=value1,key2=value2",
#         parser=opt_dict_parser,
#     ),
#     overwrite: bool = typer.Option(
#         False, help="If true, overwrite existing product file"
#     ),
# ) -> None:
#     # This logic is the same as in process_geometry, but loops through all geometries.
#     logger.info(
#         f"Processing all geometries from {geometry_provenance_url} for product {product_id}"
#     )
#     logger.info(f"Run ID: {run_id}")

#     variable_value_float = float(variable_value) if variable_value is not None else None
#     datetime = _validate_parameters(
#         variables_to_extract, datetime, datetime_string_match
#     )

#     # Get paths for writing result JSONs
#     target_store = get_store_for_url(target_location)
#     # this path includes the filename (because geometry_id is provided to get_product_path)
#     target_path = get_product_path(
#         product_id,
#         variable_name,
#         run_id=run_id,
#         # geometry_id=geometry_id,
#         datetime=datetime,
#     )

#     # TODO: refactor writing path/file code into a function. Same logic in cli_geometry_eez.py

#     # TODO: make this S3 prefix code a function.
#     if type(target_store) is S3Store:
#         # S3Store needs the full path including prefix
#         prefix = get_prefix(target_location)
#         if prefix is not None:
#             target_path = f"{prefix}/{target_path}"
#     target_url = get_url_from_store_filename(target_store, target_path)
#     logger.info(f"target_url: {target_url}")

#     # Load geometry data
#     gdf, _ = _load_geometry_data(geometry_provenance_url) # GeoDataFrame
#     logger.info(f"Found {len(gdf)} geometries")

#     # Set up Dask client. This parallelizes the processing of all geometries (which would otherwise be done sequentially and slowly).
#     client = _setup_dask_client(use_dask, dask_client_opts)

#     try:
#         for _, row in gdf.iterrows():
#             geometry_id = row["csdr-id"]
#             geometry = get_geom_from_gdf(gdf, geometry_id)
#             # TODO: Should refactor path to be built by get_product_path function.
#             path = f"{target_location}/{product_id}-{geometry_id}.json" # Is product_id needed in filename because the path includes the run-id which is more specific.

#             # TODO: replace _process_single_geometry with process_geometry function. Then we don't need to maintain two similar functions.
#             # Dask does not natively parallelize async functions. We can wrap the process_geometry call in a sync function and use dask.delayed to parallelize it if needed.

#             _process_single_geometry(
#                 geometry_id=geometry_id,
#                 geometry=geometry,
#                 run_id=run_id,
#                 variables_to_extract=variables_to_extract,
#                 dataset_provenance_url=dataset_provenance_url,
#                 datetime_string_match=datetime_string_match,
#                 variable_name=variable_name,
#                 variable_value=variable_value_float,
#                 load_kwargs=load_kwargs,
#                 product_id=product_id,
#                 geometry_provenance_url=geometry_provenance_url,
#                 datetime=datetime,
#                 target_store=target_store,
#                 path=path,
#                 overwrite=overwrite,
#             )
#     finally:
#         if client is not None:
#             client.close()

#     logger.info(f"Wrote results to {target_url}")


@products_app.command("consolidate")
def consolidate_product(
    product_id: str = typer.Option(
        "example-product", help="ID of the product being consolidated (UUID)"
    ),
    run_id: str = typer.Option(
        ..., help="ID of the product run"
    ),
    location: str = typer.Option(
        "./cache/products/gmw-v4-eez/0-0-1", help="Location to read the product files from"
    ),
    geometry_provenance_url: str = typer.Option(
        ..., help="URL that points to the geometry provenance file"
    ),
    dataset_provenance_url: str = typer.Option(
        ..., help="URL that points to the dataset provenance file"
    ),
    variable_name: str = typer.Option(
        "asset", help="Name of the variable to use for calculations (if applicable)"
    ),
    datetime: str | None = typer.Option(
        None,
        help="Parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01')",
    ),
) -> None:
    logger.info(f"Consolidating product {product_id} from {location}")
    logger.info(f"run_id {run_id}")

    store = get_store_for_url(location)

    # TODO: standardise target path logic with other functions
    path = get_product_path(product_id, variable_name, run_id, datetime)
    logger.info(f"path {path}")

    # TODO: make this S3 prefix code a function.
    if type(store) is S3Store:
        # S3Store needs the full path including prefix
        prefix = get_prefix(location)
        if prefix is not None:
            path = f"{prefix}/{path}"

    url = get_url_from_store_filename(store, path)
    logger.info(f"Looking for product files in {url}")

    # Get a list of all the json files in the product directory
    json_files = []
    chunks = store.list(path)
    for chunk in chunks:
        # Go through the chunks of file a chunk at a time
        files = [f["path"] for f in chunk if f["path"].endswith(".json")]
        json_files.extend(files)

    logger.info(f"Found {len(json_files)} product files to consolidate")

    # Load each file and combine into a pandas DataFrame
    all_data = []
    for file in json_files:
        if file.endswith(".provenance.json"):
            logger.info(f"Skipping provenance file {file}")
            continue
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
    output_file = f"{path}/{product_id}.parquet"
    write_gdf_to_parquet(df, store, output_file)

    out_url = get_url_from_store_filename(store, output_file)
    logger.info(f"Wrote consolidated product data to {out_url}")
