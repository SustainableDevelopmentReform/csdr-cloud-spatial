from json import dumps

import typer
from loguru import logger
from requests.exceptions import HTTPError
from toolz import get_in
from typer import Typer

from csdr.app_integration import (
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
    write_json,
)
from csdr.provenance import get_provenance

provenance_app = Typer()


def _meta_provenance(
    id: str,
    type: str,
    dataset_url: str,
    source_url: str | None,
    source_metadata_url: str,
    dataset_type: str,
    overwrite: bool,
    post_to_database: bool,
) -> None | str:
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

    if type not in ["geometry", "dataset"]:
        raise ValueError("Type must be 'geometry' or 'dataset'")

    provenance = get_provenance(
        id=id,
        store=store,
        path=dataset_name,
        data_url=dataset_url,
        data_type=dataset_type,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
    )

    # Write next to the dataset
    target_file = f"{dataset_name}.provenance.json"

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

        geometry_run_id = get_in(["data", "id"], response.json(), no_default=True)
        return geometry_run_id

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
    post_geometry_outputs: bool = typer.Option(
        False, help="If true, post the geometry outputs to the database"
    ),
    post_geometry_in_bulk: bool = typer.Option(
        True, help="If true, post the geometry outputs in bulk"
    ),
) -> None:
    logger.info(f"Getting provenance for geometry: {dataset_url}")

    geometry_run_id = _meta_provenance(
        id=id,
        type="geometry",
        dataset_url=dataset_url,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        dataset_type=dataset_type,
        overwrite=overwrite,
        post_to_database=post_to_database,
    )
    logger.info(f"Wrote provenance for geometry: {dataset_url}")

    logger.info(f"Geometry run ID is {geometry_run_id}")

    if post_geometry_outputs:
        if post_geometry_in_bulk:
            logger.info("Posting geometry outputs to database in bulk...")
            post_bulk_geometry_outputs_to_database(dataset_url, run_id=geometry_run_id)
        else:
            logger.info("Posting geometry outputs to database one at a time...")
            post_geometry_outputs_to_database(dataset_url, run_id=geometry_run_id)
