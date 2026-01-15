import json
import logging
import os
import sys
from typing import Any

import dateutil
import pandas as pd
import sedona.db
import typer
from dask.distributed import Client
from obstore.store import HTTPStore, LocalStore, S3Store
from odc.geo.geom import Geometry

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    read_dict,
    read_geospatial_file,
    write_gdf_to_parquet,
    write_json,
)
from csdr.products import process_variables_for_geometry
from csdr.provenance import read_provenance
from csdr.utils import CSDRException, get_geom_from_gdf, make_uuid

products_app = typer.Typer()

# In future we will get indicators/variables from the DB.
# In future variables will be called indicators and can be computed or derived (currently only computed).
# There are currently 3 types of variables:
# 1. sum-{}-area: for area-based variables
# 2. count-{}: for count-based variables
# 3. percent-{}-area: for percentage area-based variables
# There will likely be a more diverse set of variables in the future.
KNOWN_VARIABLES = [
    "sum-mangrove-area", # Used for GMW v3, GMW v4, and ACE
    "sum-seagrass-area",
    "sum-reef-area",
    "count-buildings",
    # ACE variables:
    "sum-intertidal-area",
    "sum-saltmarsh-area",
    "sum-seagrass-area",
    "percent-mangrove-area",
    "percent-intertidal-area",
    "percent-saltmarsh-area",
    "percent-seagrass-area",
]


def _validate_parameters(
    variables_to_extract: list[str],
    datetime: str | None,
    datetime_string_match: str | None,
) -> str:
    """Validate parameters and return the datetime to use."""
    if set(variables_to_extract) - set(KNOWN_VARIABLES):
        raise CSDRException(
            f"Unknown variable to extract: {variables_to_extract}. Known variables are: {KNOWN_VARIABLES}"
        )
    if datetime is None:
        if datetime_string_match is None:
            raise CSDRException(
                "Either datetime or datetime_string_match must be set to process a geometry"
            )
        else:
            datetime = datetime_string_match

    return datetime


def _setup_dask_client(
    use_dask: bool, dask_client_opts: dict[str, Any]
) -> Client | None:
    """Set up Dask client if requested."""
    if not use_dask:
        logging.info("Use Dask parameter is False. Not starting Dask client.")
        return None

    logging.info(f"Starting Dask client with options: {dask_client_opts}")

    # Parse the client opts, making them integers if they can be
    for key, value in dask_client_opts.items():
        try:
            dask_client_opts[key] = int(value)
        except ValueError:
            pass

    client = Client(**dask_client_opts)
    logging.info(
        f"Dask client started: {client.dashboard_link if hasattr(client, 'dashboard_link') else client}"
    )
    return client


def _load_geometry_data(geometry_provenance_url: str) -> tuple[Any, str]:
    """Load geometry provenance and return gdf and geometry_file_url."""
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataUrl")
    logging.info(f"Reading geometries from {geometry_file_url}")
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

# # Outdated code.
# # _process_geometry is just for local development debugging dask. It is not used in the workflow. It is a bit redundant, but helpful.
# def _process_geometry(
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
#         logging.info(
#             f"Product already exists at {target_store}/{path}, skipping processing for geometry {geometry_id}."
#         )
#         return False
    
#     # All the IO stuff is done in the parent process_all_geometries_dask, so we don't do it per geometry. It is passed in as params.

#     logging.info(f"Processing geometry id '{geometry_id}'")
#     results = process_variables_for_geometry(
#         geometry,
#         variables_to_extract,
#         dataset_provenance_url,
#         datetime_string_match=datetime_string_match,
#         variable_name=variable_name,
#         variable_value=variable_value,
#         load_kwargs=load_kwargs,
#     )
#     logging.info(f"Results for geometry {geometry_id}: {results}")

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
#     # logging.info(f"Wrote results to {target_url}")
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
    datetime: str | None = None, # This is not the current datetime but rather the parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01'). Parameter could be more explicitly named.
    geometry_id: str | None = None,
) -> str:
    path = f"{variable_name}"
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
    out_file: str = typer.Option(
        None, help="Tempfile to write list of IDs to (otherwise print to console)"
    ),
) -> None:
    logging.info(f"Dumping list of geometry ids for {geometry_provenance_url}")

    # Load the provenance file
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataUrl")

    logging.info(f"Reading geometries from {geometry_file_url}")
    gdf = read_geospatial_file(geometry_file_url)
    logging.info(f"Found {len(gdf)} geometries")

    ids_list = gdf["csdr-id"].tolist()

    # TODO: use write_json utility function?
    if out_file is not None:
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        with open(out_file, "w") as f:
            json.dump(ids_list, f, indent=4)
        logging.info(f"Wrote geometry ids to {out_file}")
    else:
        sys.stdout.write(json.dumps(ids_list, indent=4))

# Process a single geometry. Makes variables using variables_to_extract. Writes the results to a json file.
@products_app.command("process-geometry")
def process_geometry(
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
    variables_to_extract: str = typer.Option(
        ...,
        help="JSON string specifying variables and values to extract from the dataset. Example: '{\"var1\": {\"variable-name\": \"foo\", \"variable-value\": 1}, \"var2\": {\"variable-name\": \"bar\", \"variable-value\": 2}}'",
    ),
    # TODO: clarify difference between datetime_string_match and datetime
    datetime_string_match: str | None = typer.Option(
        None,
        help="If set, only process data from items whose datetime string contains this value (e.g. '2024' to get all items from 2024)",
    ),
    datetime: str | None = typer.Option(
        None,
        help="Parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01')",
    ),
    target_location: str = typer.Option(
        ...,
        help="Location to write the JSON result to",
    ),
    geometry_id: str = typer.Option(..., help="ID of the geometry to process."), # This is the actual single geometry being processed. Not to be confused with the EEZ Geometry. This is one geometry within that i.e. one EEZ e.g. Fiji.
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
        help="Options to pass to Dask client, in the form --dask-opts=\"n_workers=8,threads_per_worker=1,memory_limit=3GB\"",
        parser=opt_dict_parser,
    ),
    overwrite: bool = typer.Option(
        False, help="If true, overwrite existing product file"
    ),
) -> None:
    logging.info(f"Processing geometry id '{geometry_id}' from '{geometry_provenance_url}'")
    logging.info(f"Run ID: '{run_id}'")
    logging.info(f"variables_to_extract (raw JSON): {variables_to_extract}")

    target_location = target_location.rstrip("/") # Remove trailing slash if present

    # Parse variables_to_extract as JSON
    try:
        variables_dict = json.loads(variables_to_extract)
    except Exception as e:
        raise CSDRException(f"Failed to parse variables_to_extract as JSON: {e}")

    if not isinstance(variables_dict, dict):
        raise CSDRException("variables_to_extract must be a JSON object mapping variable keys to variable info dicts.")

    # Validate parameters (use keys for validation, e.g. 'sum-mangrove-area')
    variable_names = list(variables_dict.keys())
    datetime_val = _validate_parameters(variable_names, datetime, datetime_string_match)

    # Load geometry data using Sedona so filtering is done before loading into memory
    # TODO: Make this a function get_geometry_parquet_sedona
    sd = sedona.db.connect()
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataUrl")
    aws_region = "ap-southeast-2"  # TODO: Get this from env/config.
    sd.read_parquet(geometry_file_url, options={"aws.skip_signature": True, "aws.region": aws_region}).to_view("geometries", overwrite=True)
    geometry_df = sd.sql(f"SELECT st_srid(geometry) as crs, geometry, \"csdr-name\", \"csdr-id\" FROM geometries WHERE \"csdr-id\" = '{geometry_id}'").to_pandas()
    if len(geometry_df) == 0:
        raise CSDRException(f"Geometry id '{geometry_id}' not found in geometry file '{geometry_file_url}'")
    if len(geometry_df) > 1:
        raise CSDRException(f"Multiple geometries found for id '{geometry_id}' in geometry file '{geometry_file_url}'")
    geometry_row = geometry_df.iloc[0]
    logging.info(f"Processing geometry '{geometry_row['csdr-name']}' with id '{geometry_id}'") # Name is helpful for debugging logs.
    geometry = Geometry(geometry_row.geometry, crs=f"EPSG:{geometry_row.crs}") # ODC Geometry object. This is just one geometry.

    # Set up Dask client
    client = _setup_dask_client(use_dask, dask_client_opts)

    try:
        results = process_variables_for_geometry(
            geometry,
            variables_dict,
            dataset_provenance_url,
            datetime_string_match=datetime_string_match,
            load_kwargs=load_kwargs,
        )

        product_output = _create_product_output(
            product_id,
            geometry_id,
            geometry_provenance_url,
            dataset_provenance_url,
            datetime_val,
            results,
            run_id,
        )

        # Write output for each variable (or all in one file)
        target_store = get_store_with_prefix_from_url(target_location)

        if len(variable_names) == 1:
            variable_for_path = variable_names[0]
        else:
            variable_for_path = "many-variables"
        target_path = get_product_path(
            product_id,
            variable_for_path,
            datetime=datetime_val,
            geometry_id=geometry_id,
        )
        target_url = f"{target_location.rstrip('/')}/{target_path}"
        logging.info(f"Writing to {target_url}. target_path: {target_path}...")
        write_json(target_store, target_path, product_output)
        logging.info(f"Wrote results to {target_url}")

    finally:
        # Release resources
        if client is not None:
            client.close()


# Outdated code.
# # process-all-geometries-dask is for debugging parallel processing of all geometries. It is not used in the product workflow template.
# @products_app.command("process-all-geometries-dask")
# def process_all_geometries_dask(
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
#         ...,
#         help="Comma-separated list of variables to extract from the dataset",
#         parser=parse_csv_list
#     ),
#     # TODO: clarify difference between datetime_string_match and datetime
#     datetime_string_match: str = typer.Option(
#         None,
#         help="If set, only process data from items whose datetime string contains this value (e.g. '2024' to get all items from 2024)",
#     ),
#     datetime: str | None = typer.Option(
#         None,
#         help="Parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01')",
#     ),
#     target_location: str = typer.Option(
#         ...,
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
#         help="Options to pass to Dask client, in the form --dask-opts=\"n_workers=8,threads_per_worker=1,memory_limit=3GB\"",
#         parser=opt_dict_parser,
#     ),
#     overwrite: bool = typer.Option(
#         False, help="If true, overwrite existing product file"
#     ),
# ) -> None:
#     # This logic is the same as in process_geometry, but loops through all geometries.
#     logging.info(
#         f"Processing all geometries from {geometry_provenance_url} for product {product_id}"
#     )
#     logging.info(f"Run ID: {run_id}")

#     try:
#         variable_value = float(variable_value) if variable_value is not None else None # If variable_value can be converted to float, do so.
#     except ValueError:
#         # Else it is a string, leave it as is.
#         logging.info(f"variable_value is not parseable as a float so keeping it as a string: '{variable_value}'")
#     datetime = _validate_parameters(
#         variables_to_extract, datetime, datetime_string_match
#     )

#     # Get paths for writing result JSONs
#     target_store = get_store_with_prefix_from_url(target_location)
#     # this path includes the filename (because geometry_id is provided to get_product_path)
#     target_path = get_product_path(
#         product_id,
#         variable_name,
#         datetime=datetime,
#     )

#     target_url = f"{target_store}/{target_path}"
#     logging.info(f"target_url: {target_url}")

#     # Load geometry data
#     gdf, _ = _load_geometry_data(geometry_provenance_url) # GeoDataFrame
#     logging.info(f"Found {len(gdf)} geometries")

#     # Set up Dask client. This parallelizes the processing of all geometries (which would otherwise be done sequentially and slowly).
#     client = _setup_dask_client(use_dask, dask_client_opts)

#     try:
#         for _, row in gdf.iterrows():
#             geometry_id = row["csdr-id"]
#             logging.info(f"geometry_id: '{geometry_id}'")
#             geometry = get_geom_from_gdf(gdf, geometry_id)
#             path = get_product_path(
#                 product_id,
#                 variable_name,
#                 datetime=datetime,
#                 geometry_id=geometry_id,
#             )

#             # Dask does not natively parallelize async functions. We can wrap the process_geometry call in a sync function and use dask.delayed to parallelize it if needed.
#             _process_geometry(
#                 geometry_id=geometry_id,
#                 geometry=geometry,
#                 run_id=run_id,
#                 variables_to_extract=variables_to_extract,
#                 dataset_provenance_url=dataset_provenance_url,
#                 datetime_string_match=datetime_string_match,
#                 variable_name=variable_name,
#                 variable_value=variable_value,
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

#     logging.info(f"Wrote results to {target_url}")


@products_app.command("consolidate")
def consolidate_product(
    product_id: str = typer.Option(
        "example-product", help="ID of the product being consolidated (UUID)"
    ),
    location: str = typer.Option(
        "./cache/products/gmw-v3-eez/0-0-1/runs/test-product-run-id", help="Location to read the product files from"
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
    logging.info(f"Consolidating product {product_id} from {location}")
    location = location.rstrip("/")
    store = get_store_with_prefix_from_url(location)
    path = get_product_path(product_id, variable_name, datetime=datetime)
    logging.info(f"path {path}")
    url = f"{location}/{path}"
    logging.info(f"Looking for product files in {url}")

    # TODO: Use io.find_matching_files for this step
    # Get a list of all the json files in the product directory
    json_files = []
    chunks = store.list(path)
    for chunk in chunks:
        # Go through the chunks of file a chunk at a time
        files = [f["path"] for f in chunk if f["path"].endswith(".json")]
        json_files.extend(files)

    logging.info(f"Found {len(json_files)} product files to consolidate")

    # Load each file and combine into a pandas DataFrame
    all_data = []
    for file in json_files:
        if file.endswith(".provenance.json"):
            logging.info(f"Skipping provenance file {file}")
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
            raise CSDRException(
                f"Dataset provenance URL mismatch in {file}: {dataset_provenance_url} != {dataset_provenance_url}"
            )
        if product_id != product.get("productId", None):
            raise CSDRException(
                f"Product ID mismatch in {file}: {product_id} != {product.get('productId', None)}"
            )
        if geometry_provenance_url != geometry_provenance_url:
            raise CSDRException(
                f"Geometry provenance URL mismatch in {file}: {geometry_provenance_url} != {geometry_provenance_url}"
            )

        all_data.append(product)

    if not all_data:
        raise CSDRException(f"No valid product data found in {url}")

    df = pd.DataFrame(all_data)
    logging.info(f"Consolidated product data from {url}: {df.shape[0]} rows")

    # Write the consolidated DataFrame to a new parquet
    output_file = f"{path}/{product_id}.parquet"
    write_gdf_to_parquet(df, store, output_file)

    target_url = f"{location}/{output_file}"
    logging.info(f"Wrote consolidated product data to {target_url}")
