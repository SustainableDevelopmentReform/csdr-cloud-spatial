from json import dumps

import typer
from loguru import logger
from requests.exceptions import HTTPError
from typer import Typer
from typing import Literal

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
    dataset_url: str, # update this name to be more generic (not always a dataset)?
    dataset_type: str, # update this name to be more generic (not always a dataset)?
    overwrite: bool,
    post_to_database: bool,
    source_url: str | None = None,
    source_metadata_url: str | None = None,
    extra_info_dict: dict | None = None, # this can include runId for geometries
) -> None:
    # the json is written next to where it read from. e.g. local to local, and s3 to s3.
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
        extra_info_dict (dict | None): Additional information to include in the provenance, such as runId for geometries.

    Returns:
        None
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
    target_file = f"{dataset_name}.provenance.json" # name includes the input file extension e.g. "EEZ_land_union_v4_202410.parquet.provenance.json"

    if exists(store, target_file) and not overwrite:
        logger.warning(f"Provenance file already exists: {target_file}")
    else:
        logger.info("Either provenance file doesn't exist or it does and overwrite is on.")
        write_json(store, target_file, provenance)
        logger.info(
            f"Wrote provenance for {dataset_name} to: {dataset_url}.provenance.json"
        )

    if post_to_database:
        # should the DB write respect the overwrite flag? Currently I am not sure what would happen if something was rerun. Duplicate entries or error?
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

    _meta_provenance(
        id=id,
        type="dataset",
        dataset_url=dataset_url,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        dataset_type=dataset_type,
        overwrite=overwrite,
        post_to_database=post_to_database,
    )
    logger.info(f"Wrote provenance for dataset: {dataset_url}")


@provenance_app.command("geometry")
def write_geometry_provenance(
    id: str = typer.Option(..., help="ID of the dataset"),
    run_id: str = typer.Option(
        ...,
        help="Run ID to associate geometry outputs with",
    ), # I made run_id required instead of nullable. It can be made optional again. Alex said that it could be good for the app to define this. Not sure when this would occur yet. As I see it the whole workflow will run with one workflow-generated run id. He said "we may not want to provide the run-id, and allow it to be assigned by the app.". That still sounds like there will be a run id so I will leave it required for now.
    dataset_url: str = typer.Option(..., help="URL that points to the dataset"),
    pmtiles_url: str | None = typer.Option(
        None, help="URL that points to the PMTiles file for the geometry (optional)"
    ),
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

    extra_info_dict = {}
    extra_info_dict["geometryRunId"] = run_id
    logger.info(f"Geometry run ID is {run_id}")

    if pmtiles_url is not None: # this is optional because geometries can optionally have PMTiles
        extra_info_dict["dataPmtilesUrl"] = pmtiles_url # need to check how these are written to the db. They could be nullable fields there.

    _meta_provenance(
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

    if post_geometry_outputs:
        if post_geometry_in_bulk:
            logger.info("Posting geometry outputs to database in bulk...")
            post_bulk_geometry_outputs_to_database(
                dataset_url, run_id=run_id, batch_size=batch_size
            )
        else:
            logger.info("Posting geometry outputs to database one at a time...")
            post_geometry_outputs_to_database(dataset_url, run_id=run_id)


@provenance_app.command("product")
def write_product_provenance(
    product_url: str = typer.Option(..., help="URL that points to the product parquet"),
    product_id: str = typer.Option(..., help="Product ID"),
    dataset_run_id: str = typer.Option(
        ...,
        help="Dataset run ID",
    ),
    geometries_run_id: str = typer.Option(
        ...,
        help="Geometry run ID",
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

    product_run_id = "fancy-long-uuid-thing" # TODO: create this in the product workflow like we do in the geometry workflow
    # product run id be generated earlier like the geometry one?
    _meta_provenance(
        id=product_id,
        type="product",
        dataset_url=product_url,
        dataset_type="parquet",
        overwrite=overwrite,
        post_to_database=post_to_database,
        extra_info_dict={
            "datasetRunId": dataset_run_id,
            "geometriesRunId": geometries_run_id,
        },
    )

    # Write to DB
    if post_to_database:
        logger.info("Posting consolidated product data to database")

        for variable, output in parsed_outputs.items():
            for timePoint in output.keys():
                outputs = output[timePoint]
                logger.info(f"Posting {len(outputs)} outputs for timePoint {timePoint}")
                content = {
                    "productRunId": product_run_id,
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
