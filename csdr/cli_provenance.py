from json import dumps
from typing import Literal

import typer
from loguru import logger
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
    get_dataset_name_from_url,
    get_store_for_url,
    read_geospatial_file,
    write_json,
)
from csdr.products import parse_outputs
from csdr.provenance import get_provenance

provenance_app = Typer()

ALLOWED_PROVENANCE_TYPES = ["dataset", "geometry", "product"]


def _meta_provenance(
    id: str,
    type: Literal["dataset", "geometry", "product"],
    dataset_url: str, # Update this name to be more generic (not always a dataset)?
    dataset_type: str, # Update this name to be more generic (not always a dataset)?
    overwrite: bool,
    post_to_database: bool,
    source_url: str | None = None,
    source_metadata_url: str | None = None,
    # extra_info_dict likely includes geometriesRunId for geometries and productRunId for products
    extra_info_dict: dict | None = None,
) -> str | None:
    # The json is written next to where it read from. e.g. local to local, and s3 to s3.
    """
    Get and write provenance information for a dataset or geometry.

    Args:
        id (str): ID of the dataset or geometry or product.
        type (Literal): "dataset" or "geometry" or "product".
        dataset_url (str): URL of the dataset or geometry or product.
        dataset_type (str): Type of dataset, such as geoparquet, cloud-optimized-geotiff, zarr, etc.
        overwrite (bool): If true, overwrite existing provenance file.
        post_to_database (bool): If true, post the provenance to the database.
        source_url (str | None): URL of the original source data.
        source_metadata_url (str): URL of the source metadata.
        extra_info_dict (dict | None): Additional information to include in the provenance, such as geometriesRunId for geometries and productRunId for products. Dataset run IDs are all made by the DB.

    Returns:
        None or str: If posting to database, returns the run ID.
    """
    store = get_store_for_url(dataset_url)
    dataset_name = get_dataset_name_from_url(store, dataset_url)

    if source_url is None:
        source_url = dataset_url

    if type not in ALLOWED_PROVENANCE_TYPES:
        raise ValueError(f"Type must be one of {ALLOWED_PROVENANCE_TYPES}")

    # Build provenance dictionary
    provenance = get_provenance(
        id=id,
        store=store,
        path=dataset_name,
        data_url=dataset_url,
        data_type=dataset_type,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        extra_info_dict=extra_info_dict,
    )

    # Write json next to the input dataset/geometry/product
    target_file = f"{dataset_name}.provenance.json" # Name includes the input file extension e.g. "EEZ_land_union_v4_202410.parquet.provenance.json"

    if exists(store, target_file) and not overwrite:
        logger.warning(f"Provenance file already exists: {target_file}")
    else:
        logger.info("Either provenance file doesn't exist or it does and overwrite is on.")
        write_json(store, target_file, provenance)
        logger.info(
            f"Wrote provenance for {dataset_name} to: {dataset_url}.provenance.json"
        )

    if post_to_database:
        # Should the DB write respect the overwrite flag? Currently I am not sure what would happen if something was rerun. Duplicate entries or error?
        response = post_provenance(provenance, type=type)
        try:
            response.raise_for_status()
        except HTTPError:
            logger.exception(
                f"Failed to post provenance to database. Response was \n{dumps(response.json(), indent=2)}"
            )
        logger.info(
            f"Wrote provenance to database \n {dumps(response.json(), indent=2)}"
        )

        run_id = get_in(["data", "id"], response.json(), no_default=True)
        return run_id

    return None


@provenance_app.command("dataset")
def write_dataset_provenance(
    id: str = typer.Option(..., help="ID of the dataset"),
    dataset_url: str = typer.Option(..., help="URL that points to the dataset"),
    dataset_type: str = typer.Option(
        "not-set",
        help="Type of dataset, such as geoparquet, cloud-optimized-geotiff, zarr, etc.",
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
) -> None:
    logger.info(f"Getting provenance for dataset: {dataset_url}")

    # Datasets do not need to use run IDs in their file paths, so the run id is just created by the DB and not used elsewhere. This is because geometry runs do not create new info (unlike geometries and products).
    dataset_run_id =_meta_provenance(
        id=id,
        type="dataset",
        dataset_url=dataset_url,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        dataset_type=dataset_type,
        overwrite=overwrite,
        post_to_database=post_to_database,
    )
    logger.info(f"dataset_run_id: {dataset_run_id}")
    logger.info(f"Wrote provenance for dataset: {dataset_url}")


@provenance_app.command("geometry")
def write_geometry_provenance(
    id: str = typer.Option(..., help="ID of the geometry"),
    # run_id is always passed from the workflow.
    # It is however optional because when running this CLI command seperately from the workflow,
    # We can leave it blank and then the run_id gets created when writing to the X_run table in the DB and passed back to _meta_provenance
    run_id: str | None = typer.Option(
        None,
        help="Run ID to associate geometry outputs with",
    ),
    dataset_url: str = typer.Option(..., help="URL that points to the geometry"), # this is actually the geometry source, but calling it dataset could be good for standardisation between geometry/dataset/product. on the other hand calling it the geometry source is clearer.
    pmtiles_url: str | None = typer.Option(
        None, help="URL that points to the PMTiles file for the geometry (optional)"
    ),
    dataset_type: str = typer.Option(
        "not-set",
        help="Type of geometry, such as geoparquet, cloud-optimized-geotiff, zarr, etc.",
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
    logger.info(f"Getting provenance for geometry: {dataset_url}")

    if run_id is not None:
        logger.info(f"Run ID '{run_id}' was provided.")
    else:
        logger.info("No Run ID provided, one will be created.")

    extra_info_dict = {}
    extra_info_dict["geometriesRunId"] = run_id

    if pmtiles_url is not None: # This is optional because geometries can optionally have PMTiles
        extra_info_dict["dataPmtilesUrl"] = pmtiles_url # Need to check how these are written to the db. They could be nullable fields there instead of a loose json.

    # Should run_id be passed as a prop instead of nested in extra_info_dict?
    run_id_created = _meta_provenance(
        id=id,
        type="geometry",
        dataset_url=dataset_url,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        dataset_type=dataset_type,
        overwrite=overwrite,
        post_to_database=post_to_database,
        extra_info_dict=extra_info_dict,
    )
    logger.info(f"Wrote provenance for geometry: {dataset_url}")
    consolidated_run_id = run_id if run_id is not None else run_id_created
    logger.info(f"Consolidated geometry run ID: {consolidated_run_id}")
    if post_geometry_outputs:
        if post_geometry_in_bulk:
            logger.info("Posting geometry outputs to database in bulk...")
            post_bulk_geometry_outputs_to_database(
                dataset_url, run_id=consolidated_run_id, batch_size=batch_size
            )
        else:
            logger.info("Posting geometry outputs to database one at a time...")
            post_geometry_outputs_to_database(dataset_url, run_id=consolidated_run_id)


@provenance_app.command("product")
def write_product_provenance(
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

    run_id_created = _meta_provenance(
        id=product_id,
        type="product",
        dataset_url=product_url,
        dataset_type="parquet",
        overwrite=overwrite,
        post_to_database=post_to_database,
        extra_info_dict=extra_info_dict,
    )
    logger.info(f"Wrote provenance for product: {product_url}")
    consolidated_run_id = run_id if run_id is not None else run_id_created
    logger.info(f"Consolidated product run ID: {consolidated_run_id}")

    # Write to DB
    if post_to_database:
        logger.info("Posting consolidated product data to database")

        for variable, output in parsed_outputs.items():
            for timePoint in output.keys():
                outputs = output[timePoint]
                logger.info(f"Posting {len(outputs)} outputs for timePoint {timePoint}")
                content = {
                    "productRunId": consolidated_run_id,
                    "timePoint": timePoint,
                    "variableId": variable,
                    "outputs": outputs,
                }

                response = post_product_output_bulk(content)
                try:
                    response.raise_for_status()
                except HTTPError:
                    logger.exception(
                        f"Failed to post product output to database. Response was \n{dumps(response.json(), indent=2)}"
                    )
                else:
                    logger.info(
                        f"Posted product output for variable {variable} timePoint {timePoint}: {response.status_code}"
                    )
