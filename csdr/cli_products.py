import json
import logging
import os
import re
import sys
from enum import StrEnum
from typing import Any

import dateutil
import pandas as pd
import sedona.db
import typer
from dask.distributed import Client
from obstore.store import ObjectStore
from odc.geo.geom import Geometry

from csdr.cli_helpers import parse_csv_list
from csdr.indicators import (
    KNOWN_INDICATORS,  # noqa: F401  (re-exported for existing callers)
    unknown_indicator_message,
    unknown_indicators,
)
from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    read_dict,
    read_geospatial_file,
    split_path_and_file_name_from_url,
    write_gdf_to_parquet,
    write_json,
)
from csdr.products import process_indicators_for_geometry
from csdr.provenance import read_provenance, write_step
from csdr.utils import CSDRException, PercentileMethod, make_uuid

products_app = typer.Typer()
logger = logging.getLogger(__name__)


class PercentileMethodName(StrEnum):
    linear = "linear"
    histogram = "histogram"


# Indicator ids are validated against the registry in csdr.indicators: regex
# families (sum-<x>-area, percent-<x>-area, count-<x>, the zonal statistics
# families, and any declared family a later build implements) plus the exact-id
# allow-list KNOWN_INDICATORS, which stays importable from here for existing callers.


def _validate_parameters(
    indicators_to_extract: list[str],
    datetime: str | None,
    datetime_string_match: str | None,
) -> str:
    """Validate parameters and return the datetime to use."""
    unknown = unknown_indicators(indicators_to_extract)
    if unknown:
        raise CSDRException(unknown_indicator_message(unknown))
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
    run_id: str,
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
        "indicators": results,
        "metadata": {
            "geometryProvenanceUrl": geometry_provenance_url,
            "datasetProvenanceUrl": dataset_provenance_url,
            "command": " ".join([os.path.basename(sys.argv[0]), *sys.argv[1:]]),
        },
    }


def opt_dict_parser(s: str | dict[str, Any]) -> dict[str, Any]:
    if type(s) is dict:
        return s
    return json.loads(s)


def version_parser(s: str) -> str:
    return s.lstrip("v").replace(".", "-")


def get_product_path(
    product_id: str,
    indicator_name: str,
    datetime: str
    | None = None,  # This is not the current datetime but rather the parseable datetime to use as the timePoint for the product output (e.g. '2024-01-01T00:00:00Z' or '2024-01'). Parameter could be more explicitly named.
    geometry_id: str | None = None,
) -> str:
    path = f"{indicator_name}"
    if datetime is not None:
        path = f"{path}/{datetime}"
    # geometry id is just for the processing of single geometries
    if geometry_id is not None:
        path = f"{path}/{product_id}-{geometry_id}.json"  # Is product_id needed in filename because the path includes the run-id which is more specific.
    return path


@products_app.command("list-geometries-years")
def list_geometries_years(
    geometry_provenance_url: str = typer.Option(
        ..., help="URL that points to the geometry provenance file"
    ),
    years: str | None = typer.Option(
        ..., help="Comma-separated list of years to cross with geometries"
    ),
    exclude_ids: str = typer.Option(
        None, help="Comma-separated list of geometry IDs to exclude"
    ),
    out_file: str = typer.Option(
        None, help="Tempfile to write list of IDs to (otherwise print to console)"
    ),
    batch_size: int | None = typer.Option(
        None,
        min=1,
        help='Group ids into batches of up to N per year: [{"year": y, "ids": "a,b,c"}] for process-geometry --geometry-ids. Without it, one {"year", "id"} item per geometry (unchanged).',
    ),
) -> None:
    logger.info(f"Dumping list of geometry ids for {geometry_provenance_url}")

    # Load the provenance file
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataUrl")

    logger.info(f"Reading geometries from {geometry_file_url}")
    gdf = read_geospatial_file(geometry_file_url)
    logger.info(f"Found {len(gdf)} geometries")

    # Filter geometries by IDs.
    ids_list = gdf["csdr-id"].tolist()
    logger.info(f"Geometry contains {len(ids_list)} features.")
    if exclude_ids:
        exclude_ids_list = parse_csv_list(exclude_ids)
        ids_list = [id for id in ids_list if id not in exclude_ids_list]
        logger.info(
            f"Excluded {len(exclude_ids_list)} geometry features. {len(ids_list)} features remaining."
        )

    # Cross geometries with years.
    years_list = parse_csv_list(years)
    geometries_years = []
    for year in years_list:
        if batch_size is None:
            for id in ids_list:
                item = {"year": year, "id": id}
                geometries_years.append(item)
        else:
            for start in range(0, len(ids_list), batch_size):
                batch = ids_list[start : start + batch_size]
                geometries_years.append({"year": year, "ids": ",".join(batch)})

    if out_file is not None:
        target_path, target_file_name = split_path_and_file_name_from_url(out_file)
        target_store = get_store_with_prefix_from_url(target_path)
        write_json(target_store, target_file_name, geometries_years)
        logger.info(f"Wrote geometry ids crossed with years to {out_file}")
    else:
        sys.stdout.write(json.dumps(geometries_years, indent=4))


# Process a single geometry. Makes indicators using indicators_to_extract. Writes the results to a json file.
@products_app.command("process-geometry")
def process_geometry(
    product_id: str = typer.Option(
        "example-product", help="ID of the product being generated (UUID)"
    ),
    run_id: str = typer.Option(..., help="ID of the product run"),
    geometry_provenance_url: str = typer.Option(
        ..., help="URL that points to the geometry provenance file"
    ),
    dataset_provenance_url: str = typer.Option(
        ..., help="URL that points to the dataset provenance file"
    ),
    indicators_to_extract: str = typer.Option(
        ...,
        help='JSON string specifying indicators and values to extract from the dataset. Example: \'{"indicator1": {"indicator-name": "foo", "indicator-value": 1}, "indicator2": {"indicator-name": "bar", "indicator-value": 2}}\'',
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
    geometry_id: str | None = typer.Option(
        None, help="ID of the geometry to process. Give this or --geometry-ids."
    ),  # This is the actual single geometry being processed. Not to be confused with the EEZ Geometry. This is one geometry within that i.e. one EEZ e.g. Fiji.
    geometry_ids: str | None = typer.Option(
        None,
        help="Comma-separated geometry IDs processed one after another in this process (one Dask client), one output file each, so consolidate and --no-overwrite work as for single ids. Give this or --geometry-id.",
    ),
    load_kwargs: dict[str, str] = typer.Option(
        {},
        "--load-kwargs",
        help='Options to pass to xarray load function, in the form --load-kwargs=\'{"crs": "epsg:6933", "resolution": 100, "chunks": {"x": 2048, "y": 2048}}\'',
        parser=opt_dict_parser,
    ),
    use_dask: bool = typer.Option(
        False, help="Whether to use Dask distributed for processing"
    ),
    raster_all_touched: bool = typer.Option(
        True,
        "--raster-all-touched/--raster-cell-centre",
        help="Count every raster cell touched by the boundary (existing default), or select cells by their centres. Neither option apportions partial cell area. Recorded in the processing command.",
    ),
    percentile_method: PercentileMethodName = typer.Option(
        PercentileMethodName.linear,
        help="How p10-/p25-/p50-/p75-/p90-<x> are computed from the valid cells: 'linear' (numpy linear interpolation over the valid values) or 'histogram' (midpoint of the fixed-width bin holding the ceil(q*N)-th valid value; see --percentile-bins and --percentile-range). Recorded in the output metadata.",
    ),
    percentile_bins: int = typer.Option(
        1000,
        min=1,
        help="With --percentile-method histogram: number of equal-width bins over --percentile-range.",
    ),
    percentile_range: str = typer.Option(
        "0,1",
        help="With --percentile-method histogram: 'lo,hi' value range of the bins. Values outside it fall into the end bins (counted in the metadata as histogramClippedCells).",
    ),
    dask_client_opts: dict[str, str] = typer.Option(
        {},
        "--dask-opts",
        help='Options to pass to Dask client, in the form --dask-opts=\'{"n_workers": 8, "threads_per_worker": 1, "memory_limit": "3GB"}\'',
        parser=opt_dict_parser,
    ),
    overwrite: bool = typer.Option(
        False, help="If true, overwrite existing product file"
    ),
) -> None:
    ids = _resolve_geometry_ids(geometry_id, geometry_ids)
    percentile = PercentileMethod(
        method=str(percentile_method),
        bins=percentile_bins,
        value_range=PercentileMethod.parse_range(percentile_range),
    )
    logger.info(
        f"Processing {len(ids)} geometry id(s) {ids} from '{geometry_provenance_url}'"
    )
    logger.info(f"Run ID: '{run_id}'")
    logger.info(f"indicators_to_extract (raw JSON): {indicators_to_extract}")

    # Parse indicators_to_extract as JSON
    try:
        indicators_dict = json.loads(indicators_to_extract)
    except json.JSONDecodeError as e:
        raise CSDRException(f"Failed to parse indicators_to_extract as JSON: {e}")
    if not isinstance(indicators_dict, dict):
        raise CSDRException(
            "indicators_to_extract must be a JSON object mapping indicator keys to indicator info dicts."
        )
    # Validate parameters (use keys for validation, e.g. 'sum-mangrove-area')
    indicator_names = list(indicators_dict.keys())
    datetime_val = _validate_parameters(
        indicator_names, datetime, datetime_string_match
    )

    # Prepare target location and check for existing output and overwrite flag
    target_location = target_location.rstrip("/")  # Remove trailing slash if present
    # Write output for each indicator (or all in one file)
    target_store = get_store_with_prefix_from_url(target_location)
    if len(indicator_names) == 1:
        indicator_for_path = next(iter(indicators_dict.values()))[
            "indicator-name"
        ]  # Use the only item's indicator-name
    else:
        indicator_for_path = "many-indicators"

    # One output file per geometry, exactly as for a single id. Finished ids are
    # skipped before any source is read, so a retried batch resumes.
    pending = []
    for one_id in ids:
        target_path = get_product_path(
            product_id,
            indicator_for_path,
            datetime=datetime_val,
            geometry_id=one_id,
        )
        target_url = f"{target_location}/{target_path}"
        if exists(target_store, target_path) and not overwrite:
            logger.info(f"Product already exists at {target_url}, skipping processing.")
            continue
        pending.append((one_id, target_path, target_url))
    if not pending:
        return  # Exit successfully, nothing to do
    logger.info(
        f"{len(pending)} of {len(ids)} geometries to process (JSON missing or overwrite is True)."
    )

    # Load geometry data using Sedona so filtering is done before loading into memory
    # TODO: Make this a function get_geometry_parquet_sedona
    sd = sedona.db.connect()
    provenance = read_provenance(geometry_provenance_url)
    geometry_file_url = provenance.get("dataUrl")
    aws_region = "ap-southeast-2"  # TODO: Get this from env/config.
    sd.read_parquet(
        geometry_file_url,
        options={"aws.skip_signature": True, "aws.region": aws_region},
    ).to_view("geometries", overwrite=True)

    # Set up Dask client once for the whole batch
    client = _setup_dask_client(use_dask, dask_client_opts)

    try:
        for one_id, target_path, target_url in pending:
            _process_one_geometry(
                sd,
                geometry_file_url,
                one_id,
                target_store,
                target_path,
                target_url,
                product_id=product_id,
                run_id=run_id,
                geometry_provenance_url=geometry_provenance_url,
                dataset_provenance_url=dataset_provenance_url,
                indicators_dict=indicators_dict,
                datetime_string_match=datetime_string_match,
                datetime_val=datetime_val,
                load_kwargs=load_kwargs,
                raster_all_touched=raster_all_touched,
                percentile_method=percentile,
            )

        # No write_step here — process-geometry fans out to many pods.
        # A single summary step is written by consolidate instead.

    finally:
        # Release resources
        if client is not None:
            client.close()


def _resolve_geometry_ids(
    geometry_id: str | None, geometry_ids: str | None
) -> list[str]:
    """Exactly one of --geometry-id / --geometry-ids; a batch is unique and non-empty."""
    if (geometry_id is None) == (geometry_ids is None):
        raise CSDRException("Give exactly one of --geometry-id or --geometry-ids.")
    if geometry_id is not None:
        return [geometry_id]
    ids = [one_id.strip() for one_id in parse_csv_list(geometry_ids)]
    if not ids or any(not one_id for one_id in ids):
        raise CSDRException("--geometry-ids must be a comma-separated list of ids.")
    duplicates = sorted({one_id for one_id in ids if ids.count(one_id) > 1})
    if duplicates:
        raise CSDRException(f"--geometry-ids repeats ids: {duplicates}")
    return ids


def _process_one_geometry(
    sd: sedona.db.context.SedonaContext,
    geometry_file_url: str,
    geometry_id: str,
    target_store: ObjectStore,
    target_path: str,
    target_url: str,
    *,
    product_id: str,
    run_id: str,
    geometry_provenance_url: str,
    dataset_provenance_url: str,
    indicators_dict: dict[str, Any],
    datetime_string_match: str | None,
    datetime_val: str,
    load_kwargs: dict[str, Any],
    raster_all_touched: bool = True,
    percentile_method: PercentileMethod | None = None,
) -> None:
    """Compute and write one geometry's product JSON (the body of process-geometry)."""
    geometry_df = sd.sql(
        f'SELECT st_srid(geometry) as crs, geometry, "csdr-name", "csdr-id" FROM geometries WHERE "csdr-id" = \'{geometry_id}\''
    ).to_pandas()
    if len(geometry_df) == 0:
        raise CSDRException(
            f"Geometry id '{geometry_id}' not found in geometry file '{geometry_file_url}'"
        )
    if len(geometry_df) > 1:
        raise CSDRException(
            f"Multiple geometries found for id '{geometry_id}' in geometry file '{geometry_file_url}'"
        )
    geometry_row = geometry_df.iloc[0]
    logger.info(
        f"Processing geometry '{geometry_row['csdr-name']}' with id '{geometry_id}'"
    )  # Name is helpful for debugging logs.
    geometry_native = Geometry(
        geometry_row.geometry, crs=f"EPSG:{geometry_row.crs}"
    )  # ODC Geometry object. This is just one geometry.
    geometry_4326 = geometry_native.to_crs("EPSG:4326")

    zonal_metadata = {}
    results = process_indicators_for_geometry(
        geometry_4326,
        indicators_dict,
        dataset_provenance_url,
        datetime_string_match=datetime_string_match,
        load_kwargs=load_kwargs,
        raster_all_touched=raster_all_touched,
        percentile_method=percentile_method,
        zonal_metadata=zonal_metadata,
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
    # Zonal statistics: cell rule, cell counts per band and the percentile method.
    product_output["metadata"].update(zonal_metadata)

    logger.info(f"Writing to {target_url}. target_path: {target_path}...")
    write_json(target_store, target_path, product_output)
    logger.info(f"Wrote results to {target_url}")


def _template_process_geometry_command(command: str, first_datetime: str) -> str:
    """Replace the geometry-specific values of a process-geometry command with placeholders.

    Only the values of --geometry-id and --geometry-ids are templated (both the
    "--opt value" and "--opt=value" forms), so an id that also occurs inside
    another value is left alone.
    """
    # A batched run lists several ids; template the whole list.
    command = re.sub(r"(--geometry-ids(?:=|\s+))\S+", r"\1{geometry_ids}", command)
    command = re.sub(r"(--geometry-id(?:=|\s+))\S+", r"\1{geometry_id}", command)
    if first_datetime:
        command = command.replace(first_datetime, "{datetime}")
    return command


@products_app.command("consolidate")
def consolidate_product(
    product_id: str = typer.Option(
        "example-product", help="ID of the product being consolidated (UUID)"
    ),
    location: str = typer.Option(
        "./cache/products/gmw-v3-eez/0-0-1/runs/test-product-run-id",
        help="Location to read the product files from",
    ),
    geometry_provenance_url: str = typer.Option(
        ..., help="URL that points to the geometry provenance file"
    ),
    dataset_provenance_url: str = typer.Option(
        ..., help="URL that points to the dataset provenance file"
    ),
    indicator_name: str = typer.Option(
        "asset", help="Name of the indicator to use for calculations (if applicable)"
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing parquet file if it exists."
    ),
) -> None:
    logger.info(f"Consolidating product {product_id} from {location}")
    location = location.rstrip("/")
    store = get_store_with_prefix_from_url(location)
    path = get_product_path(product_id, indicator_name)
    logger.info(f"path {path}")
    url = f"{location}/{path}"
    output_file = f"{path}/{product_id}.parquet"
    target_url = f"{location}/{output_file}"

    # TODO: Use io.find_matching_files for this step
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
        file_geometry_provenance_url = product.get("metadata", {}).get(
            "geometryProvenanceUrl", None
        )
        file_dataset_provenance_url = product.get("metadata", {}).get(
            "datasetProvenanceUrl", None
        )

        # Do some quality checks
        if file_dataset_provenance_url != dataset_provenance_url:
            raise CSDRException(
                f"Dataset provenance URL mismatch in {file}: {file_dataset_provenance_url} != {dataset_provenance_url}"
            )
        if product_id != product.get("productId", None):
            raise CSDRException(
                f"Product ID mismatch in {file}: {product_id} != {product.get('productId', None)}"
            )
        if file_geometry_provenance_url != geometry_provenance_url:
            raise CSDRException(
                f"Geometry provenance URL mismatch in {file}: {file_geometry_provenance_url} != {geometry_provenance_url}"
            )

        all_data.append(product)

    if not all_data:
        raise CSDRException(f"No valid product data found in {url}")

    df = pd.DataFrame(all_data)
    logger.info(f"Consolidated product data from {url}: {df.shape[0]} rows")

    if exists(store, output_file) and not overwrite:
        logger.info(
            f"Consolidated product already exists at {target_url}, skipping writing consolidated data."
        )
    else:
        logger.info(f"Looking for product files in {url}")

        # Write the consolidated DataFrame to a new parquet
        write_gdf_to_parquet(df, store, output_file)

        logger.info(f"Wrote consolidated product data to {target_url}")

    # Extract the unique years from the processed geometry data
    years = sorted(
        {item.get("timePoint", "")[:4] for item in all_data if item.get("timePoint")}
    )

    # Read the example process-geometry command from the first product's metadata.
    # Any single example is sufficient since they all share the same structure.
    # Replace the geometry-specific values with placeholders to show the template.
    first_product = all_data[0]
    process_geometry_command = first_product.get("metadata", {}).get(
        "command", "csdr products process-geometry"
    )
    first_datetime = first_product.get("timePoint", "")[:4]  # e.g. "2020"
    process_geometry_command = _template_process_geometry_command(
        process_geometry_command, first_datetime
    )

    # Write a summary step for the fan-out process-geometry work.
    # Each geometry is processed in its own pod, so we record a single
    # summary step here where the count is known.
    geom_count = int(len(all_data) / len(years))  # Assuming all geometries have a year
    write_step(
        label=f"Process {int(len(all_data) / len(years))} geometr{'ies' if geom_count != 1 else 'y'} for {len(years)} year{'s' if len(years) != 1 else ''} to compute indicators",
        inputs={
            "geometry_provenance_url": geometry_provenance_url,
            "dataset_provenance_url": dataset_provenance_url,
            "indicator_name": indicator_name,
            "years": years,
        },
        outputs={"geometries_processed": geom_count},
        source_function=process_geometry,
        command=process_geometry_command,
    )
    write_step(
        label="Consolidate per-geometry-and-year results into a single product parquet",
        inputs={
            "location": location,
            "indicator_name": indicator_name,
        },
        outputs={"target_url": target_url},
    )
