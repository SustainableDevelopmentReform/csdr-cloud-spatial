from json import dumps

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
    type: str,
    dataset_url: str,
    dataset_type: str,
    overwrite: bool,
    post_to_database: bool,
    source_url: str | None = None,
    source_metadata_url: str | None = None,
    extra_info_dict: dict | None = None,
) -> None | str:
    # Does this write the json next to where it read from? e.g. local to local, and s3 to s3.
    # What is extra_info_dict? What can I expect?
    """
    Get and write provenance information for a dataset or geometry.

    Args:
        id (str): ID of the dataset or geometry.
        type (str): "dataset" or "geometry".
        dataset_url (str): URL of the dataset or geometry.
        source_url (str | None): URL of the original source data.
        source_metadata_url (str): URL of the source metadata.
        dataset_type (str): Type of dataset, such as geoparquet, cloud-optimized-geotiff, zarr, etc.
        overwrite (bool): If true, overwrite existing provenance file.
        post_to_database (bool): If true, post the provenance to the database.

    Returns:
        None or str: If posting to database and type is "geometry", returns the geometry run ID.
    """
    store = get_store_for_url(dataset_url)
    dataset_name = get_dataset_name_from_url(store, dataset_url)

    if source_url is None:
        source_url = dataset_url

    if type not in ALLOWED_PROVENANCE_TYPES:
        raise ValueError(f"Type must be one of {ALLOWED_PROVENANCE_TYPES}")

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

    # Write next to the dataset
    target_file = f"{dataset_name}.provenance.json" # should this include the .parquet file?

    if exists(store, target_file) and not overwrite:
        logger.warning(f"Provenance file already exists: {target_file}")
    else:
        write_json(store, target_file, provenance)
        logger.info(
            f"Wrote provenance for {dataset_name} to: {dataset_url}.provenance.json"
        )

    if post_to_database:
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
    run_id: str | None = typer.Option(
        None,
        help="Run ID to associate geometry outputs with",
    ), # why is run id nullable? It seems to me that it should be required
    dataset_url: str = typer.Option(..., help="URL that points to the dataset"),
    pmtiles_url: str | None = typer.Option(
        None, help="URL that points to the PMTiles file for the geometry"
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

    if run_id is not None:
        extra_info_dict["runId"] = run_id

    if pmtiles_url is not None:
        extra_info_dict["dataPmtilesUrl"] = pmtiles_url

    geometry_run_id = _meta_provenance(
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

    logger.info(f"Geometry run ID is {geometry_run_id}")

    if post_geometry_outputs:
        if post_geometry_in_bulk:
            logger.info("Posting geometry outputs to database in bulk...")
            post_bulk_geometry_outputs_to_database(
                dataset_url, run_id=geometry_run_id, batch_size=batch_size
            )
        else:
            logger.info("Posting geometry outputs to database one at a time...")
            post_geometry_outputs_to_database(dataset_url, run_id=geometry_run_id)


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

    product_run_id = _meta_provenance(
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
