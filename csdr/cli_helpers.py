import logging
import os
from datetime import datetime

import typer

from csdr.io import (
    get_store_with_prefix_from_url,
    read_geospatial_file,
    split_path_and_file_name_from_url,
    write_gdf_to_parquet,
)
from csdr.provenance import write_step
from csdr.utils import CSDRException, make_uuid

helpers_app = typer.Typer()
logger = logging.getLogger(__name__)


def parse_csv_list(value: str) -> list[str]:
    return value.split(",") if value else []


@helpers_app.command("create-run-id")
def create_run_id() -> None:
    logger.info("Creating run ID...")

    now = datetime.now().isoformat()
    run_id = make_uuid(now)
    os.makedirs("/tmp", exist_ok=True)
    with open("/tmp/run_id.txt", "w") as f:
        f.write(run_id)
    logger.info(f"Run ID {run_id} written to /tmp/run_id.txt")


@helpers_app.command("filter-geometries-by-name")
def filter_geometries_by_name(
    source_url: str = typer.Option(
        ..., help="URL of the source parquet file containing geometries."
    ),
    target_url: str = typer.Option(
        ..., help="URL of where to write the output parquet file."
    ),
    name_fields: str = typer.Option(
        "csdr-name,SOVEREIGN1,SOVEREIGN2",
        help="Comma-separated column names used to match geometry names.",
    ),
    geometry_names: str = typer.Option(
        ...,
        help="Comma-separated geometry names to keep (must all exist).",
    ),
) -> None:
    """
    Docstring for filter_geometries_by_name

    :param source_url: The URL of the source parquet file containing geometries to filter.
    :type source_url: str
    :param target_url: The URL of where to write the output parquet file.
    :type target_url: str
    :param name_fields: Comma-separated column names used to match geometry names.
    :type name_fields: str
    :param geometry_names: Comma-separated list of geometry names to filter by.
    :type geometry_names: str
    """
    logger.info("Filtering geometries by name...")

    geometry_names_list = parse_csv_list(geometry_names)
    name_fields_list = parse_csv_list(name_fields)

    gdf = read_geospatial_file(source_url)

    for name_field in name_fields_list:
        if name_field not in gdf.columns:
            raise CSDRException(
                f"Field '{name_field}' not found in the GeoDataFrame columns."
            )

    requested_names = [name.strip() for name in geometry_names_list if name.strip()]
    requested_names_set = set(requested_names)

    # Validate requested names exist in at least one of the name fields
    available_names = set()
    for name_field in name_fields_list:
        available_names.update(
            gdf[name_field].dropna().astype(str).str.strip().unique()
        )

    missing_names = sorted(requested_names_set - available_names)
    if missing_names:
        raise CSDRException(
            f"These geometry names were not found in any of {name_fields_list}: {missing_names}"
        )

    # Keep rows where any name field matches a requested name
    match_mask = False
    for name_field in name_fields_list:
        match_mask = match_mask | gdf[name_field].astype(str).str.strip().isin(
            requested_names_set
        )

    filtered_gdf = gdf[match_mask]

    # Reset csdr-id (DB geometry_output.id has a unique constraint that this filtered dataset must not violate).
    timestamp = datetime.now().isoformat()
    filtered_gdf["csdr-id"] = [
        make_uuid(source_url + timestamp + str(i)) for i in range(len(filtered_gdf))
    ]

    target_path, target_filename = split_path_and_file_name_from_url(target_url)
    target_store = get_store_with_prefix_from_url(target_path)
    write_gdf_to_parquet(filtered_gdf, target_store, target_filename)
    logger.info(f"Filtered geometries written to {target_url}")
    write_step(
        label="Filter geometries by name from source parquet",
        inputs={"source_url": source_url, "geometry_names": geometry_names},
        outputs={"target_url": target_url},
    )
