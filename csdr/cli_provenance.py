import os
from urllib.parse import urlparse

import typer
from loguru import logger
from obstore.store import S3Store
from typer import Typer

from csdr.io import exists, get_s3_prefix, get_store_for_url, write_json
from csdr.provenance import get_dataset_provenance

provenance_app = Typer()


@provenance_app.command("dataset")
def write_dataset_provenance(
    id: str = typer.Option(..., help="Name of the dataset"),
    dataset_url: str = typer.Option(..., help="URL that points to the dataset"),
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
    """Get the provenance information for a dataset."""
    logger.info(f"Getting provenance for dataset: {dataset_url}")

    store = get_store_for_url(dataset_url)
    parsed_url = urlparse(dataset_url)
    # Get the file component of the path
    dataset_name = os.path.basename(parsed_url.path)

    s3_prefix = None
    if type(store) is S3Store:
        s3_prefix = get_s3_prefix(dataset_url)
        dataset_name = f"{s3_prefix}/{dataset_name}"

    if source_url is None:
        source_url = dataset_url

    logger.info(f"Dataset name: {dataset_name}")

    provenance = get_dataset_provenance(
        id=id,
        store=store,
        path=dataset_name,
        dataset_url=dataset_url,
        source_url=source_url,
        source_metadata_url=source_metadata_url,
        dataset_type="not-set",
    )
    logger.info(provenance)

    # Write next to the dataset
    target_file = f"{dataset_name}.provenance.json"
    logger.info(f"Target provenance file: {target_file}")

    if exists(store, target_file) and not overwrite:
        logger.warning(f"Provenance file already exists: {target_file}")
    else:
        logger.info(f"Writing provenance to: {target_file}")
        write_json(store, target_file, provenance)
        logger.info(f"Wrote provenance for dataset to: {dataset_url}.provenance.json")

    if post_to_database:
        logger.warning("Posting to database is not yet implemented")
