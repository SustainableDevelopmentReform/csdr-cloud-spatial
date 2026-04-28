import logging
from json import dumps, loads
from typing import Literal

import typer
from requests.exceptions import HTTPError
from toolz import get_in
from typer import Typer

from csdr.app_integration import (
    post_product_output_bulk,
    post_provenance,
)
from csdr.geometries import (
    post_bulk_geometry_outputs_to_database,
    post_geometry_outputs_to_database,
)
from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    read_geospatial_file,
    split_path_and_file_name_from_url,
    write_json,
)
from csdr.products import parse_outputs
from csdr.provenance import clear_steps, get_provenance, read_steps
from csdr.utils import CSDRException

provenance_app = Typer()
logger = logging.getLogger(__name__)

ALLOWED_PROVENANCE_TYPES = ["dataset", "geometry", "product"]


def _meta_provenance(
    id: str,
    type: Literal["dataset", "geometry", "product"],
    data_url: str,
    data_type: str,  # TODO: make this a Literal[...]
    overwrite: bool,
    post_to_database: bool,
    source_url: str | None = None,
    source_metadata_url: str | None = None,
    workflow_dag: list | None = None,
    # extra_info_dict likely includes geometriesRunId for geometries and productRunId for products
    extra_info_dict: dict | None = None,
) -> str | None:
    # The json is written next to where it read from. e.g. local to local, and s3 to s3.
    """
    Get and write provenance information for a dataset or geometry.

    Args:
        id (str): ID of the dataset or geometry or product.
        type (Literal): "dataset" or "geometry" or "product".
        data_url (str): URL of the dataset or geometry or product.
        data_type (str): Type of dataset, such as geoparquet, cloud-optimized-geotiff, zarr, etc.
        overwrite (bool): If true, overwrite existing provenance file.
        post_to_database (bool): If true, post the provenance to the database.
        source_url (str | None): URL of the original source data.
        source_metadata_url (str): URL of the source metadata.
        workflow_dag (list | None): List of workflow step objects for provenance.
        extra_info_dict (dict | None): Additional information to include in the provenance, such as geometriesRunId for geometries and productRunId for products. Dataset run IDs are all made by the DB.

    Returns:
        None or str: If posting to database, returns the run ID.
    """
    path, file_name = split_path_and_file_name_from_url(data_url)
    store = get_store_with_prefix_from_url(path)

    if source_url is None:
        source_url = data_url

    if type not in ALLOWED_PROVENANCE_TYPES:
        raise CSDRException(f"Type must be one of {ALLOWED_PROVENANCE_TYPES}")

    # Build provenance dictionary
    provenance = get_provenance(
        id=id,
        store=store,
        file_name=file_name,
        data_url=data_url,
        data_type=data_type,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        workflow_dag=workflow_dag,
        extra_info_dict=extra_info_dict,
    )

    # Write json next to the input dataset/geometry/product
    target_file = f"{file_name}.provenance.json"  # Name includes the input file extension e.g. "EEZ_land_union_v4_202410.parquet.provenance.json"

    if exists(store, target_file) and not overwrite:
        logger.warning(f"Provenance file already exists: {target_file}")
    else:
        logger.info(
            "Either provenance file doesn't exist or it does and overwrite is on."
        )
        write_json(store, target_file, provenance)
        logger.info(f"Wrote provenance for {file_name} to: {data_url}.provenance.json")

    if post_to_database:
        # Should the DB write respect the overwrite flag? Currently I am not sure what would happen if something was rerun. Duplicate entries or error?
        response = post_provenance(provenance, type=type)
        try:
            response.raise_for_status()
        except HTTPError:
            logger.exception(
                f"Failed to post provenance to database. Response was: \n{dumps(response.json(), indent=2)}",
            )
            raise
        logger.info(
            f"Wrote provenance to database \n {dumps(response.json(), indent=2)}"
        )

        run_id = get_in(["data", "id"], response.json(), no_default=True)
        return run_id

    return None


@provenance_app.command("dataset")
def _write_dataset_provenance(
    id: str = typer.Option(..., help="ID of the dataset"),
    dataset_url: str = typer.Option(..., help="URL that points to the dataset"),
    dataset_type: str = typer.Option(
        "not-set",
        help="Type of dataset, such as geoparquet, cloud-optimized-geotiff, zarr, etc.",
    ),
    pmtiles_url: str | None = typer.Option(
        None, help="URL that points to the PMTiles file for the dataset (optional)"
    ),
    source_metadata_url: str = typer.Option(
        ...,
        help="URL of the source metadata, such as https://example.com/metadata.html",
    ),
    source_url: str | None = typer.Option(
        None,
        help="URL of the original source data, such as https://example.com/data.tif",
    ),
    overwrite: bool = typer.Option(
        False, help="If true, overwrite existing provenance file"
    ),
    post_to_database: bool = typer.Option(
        False, help="If true, post the provenance to the database"
    ),
    workflow_dag: str = typer.Option(
        None,
        help="Workflow DAG as a JSON array of step objects. If not provided, reads from local provenance step files.",
    ),
) -> None:
    logger.info(f"Getting provenance for dataset: {dataset_url}")

    workflow_dag_parsed = loads(workflow_dag) if workflow_dag else read_steps()

    extra_info_dict = {}
    if (
        pmtiles_url is not None
    ):  # This is optional because geometries and datasets can optionally have PMTiles
        extra_info_dict["dataPmtilesUrl"] = pmtiles_url

    # Datasets do not need to use run IDs in their file paths, so the run id is just created by the DB and not used elsewhere. This is because geometry runs do not create new info (unlike geometries and products).
    dataset_run_id = _meta_provenance(
        id=id,
        type="dataset",
        data_url=dataset_url,
        data_type=dataset_type,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        overwrite=overwrite,
        post_to_database=post_to_database,
        workflow_dag=workflow_dag_parsed,
        extra_info_dict=extra_info_dict,  # extra_info_dict can contain dataPmtilesUrl (needed for ACA Reef dataset)
    )
    clear_steps()
    logger.info(f"dataset_run_id: {dataset_run_id}")
    logger.info(f"Wrote provenance for dataset: {dataset_url}")


@provenance_app.command("geometry")
def _write_geometry_provenance(
    id: str = typer.Option(..., help="ID of the geometry"),
    # run_id is always passed from the workflow.
    # It is however optional because when running this CLI command seperately from the workflow,
    # We can leave it blank and then the run_id gets created when writing to the X_run table in the DB and passed back to _meta_provenance
    run_id: str | None = typer.Option(
        None,
        help="Run ID to associate geometry outputs with",
    ),
    geometry_url: str = typer.Option(..., help="URL that points to the geometry"),
    geometry_type: str = typer.Option(
        "not-set",
        help="Type of geometry, such as geoparquet, cloud-optimized-geotiff, zarr, etc.",
    ),
    pmtiles_url: str | None = typer.Option(
        None, help="URL that points to the PMTiles file for the geometry (optional)"
    ),
    source_metadata_url: str = typer.Option(
        ...,
        help="URL of the source metadata, such as https://example.com/metadata.html",
    ),
    source_url: str | None = typer.Option(
        None,
        help="URL of the original source data, such as https://example.com/data.tif",
    ),
    overwrite: bool = typer.Option(
        False, help="If true, overwrite existing provenance file"
    ),
    post_to_database: bool = typer.Option(
        False, help="If true, post the provenance to the database"
    ),
    workflow_dag: str = typer.Option(
        None,
        help="Workflow DAG as a JSON array of step objects. If not provided, reads from local provenance step files.",
    ),
    post_geometry_outputs: bool = typer.Option(
        False, help="If true, post the geometry outputs to the database"
    ),
    post_geometry_in_bulk: bool = typer.Option(
        True, help="If true, post the geometry outputs in bulk"
    ),
    batch_size: int = typer.Option(
        50, help="Batch size for posting geometry outputs in bulk"
    ),
) -> None:
    logger.info(f"Getting provenance for geometry: {geometry_url}")

    workflow_dag_parsed = loads(workflow_dag) if workflow_dag else read_steps()

    if run_id is not None:
        logger.info(f"Run ID '{run_id}' was provided.")
    else:
        logger.info("No Run ID provided, one will be created.")

    extra_info_dict = {}
    extra_info_dict["geometriesRunId"] = run_id

    if (
        pmtiles_url is not None
    ):  # This is optional because geometries and datasets can optionally have PMTiles
        extra_info_dict["dataPmtilesUrl"] = pmtiles_url

    # Should run_id be passed as a prop instead of nested in extra_info_dict?
    run_id_created = _meta_provenance(
        id=id,
        type="geometry",
        data_url=geometry_url,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        data_type=geometry_type,
        overwrite=overwrite,
        post_to_database=post_to_database,
        extra_info_dict=extra_info_dict,
        workflow_dag=workflow_dag_parsed,
    )
    logger.info(f"Wrote provenance for geometry: {geometry_url}")
    consolidated_run_id = run_id if run_id is not None else run_id_created
    logger.info(f"Consolidated geometry run ID: {consolidated_run_id}")
    if post_geometry_outputs:
        if post_geometry_in_bulk:
            logger.info("Posting geometry outputs to database in bulk...")
            post_bulk_geometry_outputs_to_database(
                geometry_url, run_id=consolidated_run_id, batch_size=batch_size
            )
        else:
            logger.info("Posting geometry outputs to database one at a time...")
            post_geometry_outputs_to_database(geometry_url, run_id=consolidated_run_id)
    clear_steps()


@provenance_app.command("product")
def _write_product_provenance(
    product_url: str = typer.Option(..., help="URL that points to the product parquet"),
    product_id: str = typer.Option(..., help="Product ID"),
    run_id: str | None = typer.Option(
        None,
        help="Product run ID to associate product outputs with",
    ),
    dataset_run_id: str = typer.Option(
        ...,
        help="Dataset run ID",
    ),
    geometries_run_id: str = typer.Option(
        ...,
        help="Geometries run ID",
    ),
    workflow_dag: str = typer.Option(
        None,
        help="Workflow DAG as a JSON array of step objects. If not provided, reads from local provenance step files.",
    ),
    post_to_database: bool = typer.Option(
        False, help="If true, post the provenance to the database"
    ),
    overwrite: bool = typer.Option(
        False, help="If true, overwrite existing provenance file"
    ),
) -> None:
    logger.info(f"Getting provenance for product: {product_url}")
    df = read_geospatial_file(product_url)
    parsed_outputs = parse_outputs(df)

    workflow_dag_parsed = loads(workflow_dag) if workflow_dag else read_steps()

    if run_id is not None:
        logger.info(f"Run ID '{run_id}' was provided.")
    else:
        logger.info("No Run ID provided, one will be created.")

    extra_info_dict = {
        "datasetRunId": dataset_run_id,
        "geometriesRunId": geometries_run_id,
    }
    if run_id is not None:
        extra_info_dict["productRunId"] = run_id

    # Post to product_run table. Get ID (created if none was provided).
    run_id_created = _meta_provenance(
        id=product_id,
        type="product",
        data_url=product_url,
        data_type="parquet",
        overwrite=overwrite,
        post_to_database=post_to_database,
        workflow_dag=workflow_dag_parsed,
        extra_info_dict=extra_info_dict,
    )
    logger.info(f"Wrote provenance for product: {product_url}")
    consolidated_run_id = run_id if run_id is not None else run_id_created
    logger.info(f"Consolidated product run ID: {consolidated_run_id}")

    # Write to DB
    if post_to_database:
        logger.info("Posting consolidated product data to database")

        for indicator, output in parsed_outputs.items():
            for timePoint in output.keys():
                outputs = output[timePoint]
                logger.info(f"Posting {len(outputs)} outputs for timePoint {timePoint}")
                content = {
                    "productRunId": consolidated_run_id,
                    "timePoint": timePoint,
                    "indicatorId": indicator,
                    "outputs": outputs,
                }

                response = post_product_output_bulk(content)
                try:
                    response.raise_for_status()
                except HTTPError as e:
                    logger.exception(
                        f"Failed to post product output to database.\nError: {e}\nResponse was: \n{dumps(response.json(), indent=2)}",
                    )
                    raise
                else:
                    logger.info(
                        f"Posted product output for indicator {indicator} timePoint {timePoint}: {response.status_code}"
                    )
    clear_steps()
