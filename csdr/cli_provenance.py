import typer
from loguru import logger
from typer import Typer

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
    dataset_url: str,
    source_url: str | None,
    source_metadata_url: str,
    dataset_type: str,
    overwrite: bool,
    post_to_database: bool,
) -> None:
    store = get_store_for_url(dataset_url)
    dataset_name = get_dataset_name_from_url(store, dataset_url)

    if source_url is None:
        source_url = dataset_url

    provenance = get_provenance(
        id=id,
        store=store,
        path=dataset_name,
        dataset_url=dataset_url,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        dataset_type=dataset_type,
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
        logger.warning("Posting to database is not yet implemented")


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
) -> None:
    logger.info(f"Getting provenance for geometry: {dataset_url}")

    _meta_provenance(
        id=id,
        dataset_url=dataset_url,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        dataset_type=dataset_type,
        overwrite=overwrite,
        post_to_database=post_to_database,
    )
    logger.info(f"Wrote provenance for geometry: {dataset_url}")
