import logging
from json import dumps
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
from csdr.provenance import get_provenance
from csdr.utils import CSDRException

provenance_app = Typer()

ALLOWED_PROVENANCE_TYPES = ["dataset", "geometry", "product"]


def _meta_provenance(
    id: str,
    type: Literal["dataset", "geometry", "product"],
    data_url: str,
    data_type: Literal["geoparquet", "stac-geoparquet"],
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
        data_url (str): URL of the dataset or geometry or product.
        data_type (Literal): Type of dataset. 'geoparquet', 'stac-geoparquet'.
        overwrite (bool): If true, overwrite existing provenance file.
        post_to_database (bool): If true, post the provenance to the database.
        source_url (str | None): URL of the original source data.
        source_metadata_url (str): URL of the source metadata.
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
        extra_info_dict=extra_info_dict,
    )

    # Write json next to the input dataset/geometry/product
    target_file = f"{file_name}.provenance.json"  # Name includes the input file extension e.g. "EEZ_land_union_v4_202410.parquet.provenance.json"

    if exists(store, target_file) and not overwrite:
        logging.warning(f"Provenance file already exists: {target_file}")
    else:
        logging.info(
            "Either provenance file doesn't exist or it does and overwrite is on."
        )
        write_json(store, target_file, provenance)
        logging.info(f"Wrote provenance for {file_name} to: {data_url}.provenance.json")

    if post_to_database:
        # Should the DB write respect the overwrite flag? Currently I am not sure what would happen if something was rerun. Duplicate entries or error?
        response = post_provenance(provenance, type=type)
        try:
            response.raise_for_status()
        except HTTPError:
            logging.exception(
                f"Failed to post provenance to database. Response was: \n{dumps(response.json(), indent=2)}",
            )
            raise
        logging.info(
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
    logging.info(f"Getting provenance for dataset: {dataset_url}")

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
    )
    logging.info(f"dataset_run_id: {dataset_run_id}")
    logging.info(f"Wrote provenance for dataset: {dataset_url}")


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
    geometry_url: str = typer.Option(..., help="URL that points to the geometry"),
    geometry_type: Literal["geoparquet", "stac-geoparquet"] = typer.Option(
        "not-set",
        help="Type of geometry. Can be 'geoparquet' or 'stac-geoparquet'.",
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
    logging.info(f"Getting provenance for geometry: {geometry_url}")

    if run_id is not None:
        logging.info(f"Run ID '{run_id}' was provided.")
    else:
        logging.info("No Run ID provided, one will be created.")

    extra_info_dict = {}
    extra_info_dict["geometriesRunId"] = run_id

    if (
        pmtiles_url is not None
    ):  # This is optional because geometries can optionally have PMTiles
        extra_info_dict["dataPmtilesUrl"] = (
            pmtiles_url  # Need to check how these are written to the db. They could be nullable fields there instead of a loose json.
        )

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
    )
    logging.info(f"Wrote provenance for geometry: {geometry_url}")
    consolidated_run_id = run_id if run_id is not None else run_id_created
    logging.info(f"Consolidated geometry run ID: {consolidated_run_id}")
    if post_geometry_outputs:
        if post_geometry_in_bulk:
            logging.info("Posting geometry outputs to database in bulk...")
            post_bulk_geometry_outputs_to_database(
                geometry_url, run_id=consolidated_run_id, batch_size=batch_size
            )
        else:
            logging.info("Posting geometry outputs to database one at a time...")
            post_geometry_outputs_to_database(geometry_url, run_id=consolidated_run_id)


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
    logging.info(f"Getting provenance for product: {product_url}")
    df = read_geospatial_file(product_url)
    parsed_outputs = parse_outputs(df)

    if run_id is not None:
        logging.info(f"Run ID '{run_id}' was provided.")
    else:
        logging.info("No Run ID provided, one will be created.")

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
        extra_info_dict=extra_info_dict,
    )
    logging.info(f"Wrote provenance for product: {product_url}")
    consolidated_run_id = run_id if run_id is not None else run_id_created
    logging.info(f"Consolidated product run ID: {consolidated_run_id}")

    # Write to DB
    if post_to_database:
        logging.info("Posting consolidated product data to database")

        for indicator, output in parsed_outputs.items():
            for timePoint in output.keys():
                outputs = output[timePoint]
                logging.info(
                    f"Posting {len(outputs)} outputs for timePoint {timePoint}"
                )
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
                    logging.exception(
                        f"Failed to post product output to database.\nError: {e}\nResponse was: \n{dumps(response.json(), indent=2)}",
                    )
                    raise
                else:
                    logging.info(
                        f"Posted product output for indicator {indicator} timePoint {timePoint}: {response.status_code}"
                    )
