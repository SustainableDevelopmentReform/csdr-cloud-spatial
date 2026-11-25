import logging
from pathlib import PurePosixPath

import typer

from csdr.cli_helpers import parse_csv_list
from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    split_path_and_file_name_from_url,
)
from csdr.provenance import write_step
from csdr.utils import CSDRException
from csdr.vector_convert import (
    _get_geometry_id,
    convert_vector,
    method_notes,
    step_inputs,
    step_outputs,
)

conversion_app = typer.Typer()
logger = logging.getLogger(__name__)


@conversion_app.command("zip-to-parquet")
def convert_zipfile_to_parquet(
    source_zip_location: str = typer.Option(
        help="Local or remote path (local or s3://) to the zip file containing the geospatial data.",
        default="./cache/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip",  # EEZ is just an example
    ),
    source_internal_path_name: str = typer.Option(
        help="The internal path within the zip file to the data to extract.",
        default="EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp",  # EEZ is just an example
    ),
    # run_id is already built into target_location
    target_location: str = typer.Option(
        help="Local or remote path (local or s3://) to store the converted file.",
        default="./cache/eez-v4/0-0-1/runs/fancy-long-uuid-thing",
    ),
    name_field: str = typer.Option(
        "SOVEREIGN1", help="The field in the data to use for the 'Name' attribute."
    ),
    geometry_id: str | None = typer.Option(
        None,
        help="Value to use for the id. Should be kebab-case, no spaces. Defaults to None, which uses the filename.",
    ),
    create_pmtiles: bool = typer.Option(
        True, help="If true, create a PMTiles file alongside the parquet."
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing parquet file if it exists."
    ),
) -> None:
    logger.info("Starting parquet conversion process...")

    assert source_zip_location.endswith(".zip"), "Source file must be a .zip file"

    source_path, source_zip_name = split_path_and_file_name_from_url(
        source_zip_location
    )
    source_store = get_store_with_prefix_from_url(source_path)

    # Check if source zip exists
    if not exists(source_store, source_zip_name):
        raise CSDRException(
            f"Source zip file does not exist at {source_zip_location}. Cannot extract."
        )
    logger.info(
        f"Source zip file found at {source_zip_location}, proceeding with extraction."
    )

    target_location = target_location.rstrip("/")
    # Shapefile, GeoPackage or GeoJSON member: the output takes the member's
    # name with a .parquet extension (for a .shp this is the historical name).
    target_filename = f"{PurePosixPath(source_internal_path_name).stem}.parquet"
    target_url = f"{target_location}/{target_filename}"

    # Same engine as vector-to-geoparquet. Historical behaviour is kept: no
    # geometry repair, no partitions, and only csdr-id/csdr-name in the PMTiles.
    result = convert_vector(
        source_zip_location,
        target_location,
        target_filename=target_filename,
        zip_member=source_internal_path_name,
        name_field=name_field,
        geometry_id=_get_geometry_id(geometry_id, source_internal_path_name),
        create_pmtiles=create_pmtiles,
        pmtiles_fields=["csdr-id", "csdr-name"],
        make_valid=False,
        overwrite=overwrite,
        drop_empty=False,
    )
    if result.skipped:
        logger.info(
            f"Target parquet file already exists at {target_url} and overwrite is off. Use --overwrite to replace. Exiting successfully."
        )
    else:
        logger.info(f"Parquet extraction process completed. Wrote file to {target_url}")

    kind = {
        ".shp": "shapefile",
        ".gpkg": "GeoPackage",
        ".geojson": "GeoJSON",
        ".json": "GeoJSON",
    }.get(PurePosixPath(source_internal_path_name).suffix.lower(), "vector data")
    inputs = {
        "source_zip_location": source_zip_location,
        "source_internal_path_name": source_internal_path_name,
    }
    if result.source_sha256 is not None:
        inputs["source_sha256"] = result.source_sha256
    # Write step regardless of whether we skipped or did the work.
    write_step(
        label=f"Convert zipped {kind} to GeoParquet{' and PMTiles' if create_pmtiles else ''}",
        inputs=inputs,
        outputs={"target_url": target_url},
    )


@conversion_app.command("vector-to-geoparquet")
def convert_vector_to_geoparquet(
    source: str = typer.Option(
        ...,
        help="Local path or store URL (s3://, https://) of a .shp, .zip, .gpkg, .geojson/.json or GeoParquet .parquet file.",
    ),
    target_location: str = typer.Option(
        ..., help="Local or s3:// folder for <slug>.parquet (and partition/, .pmtiles)."
    ),
    target_filename: str | None = typer.Option(
        None,
        help="Output slug without extension. Defaults to the source (or zip member) file name.",
    ),
    zip_member: str | None = typer.Option(
        None,
        help="Path inside a .zip source to read. Defaults to the only vector member.",
    ),
    layer: str | None = typer.Option(
        None, help="Layer to read from a multi-layer source such as a GeoPackage."
    ),
    where: str | None = typer.Option(
        None,
        help="SQL WHERE clause applied while reading, e.g. \"STATE_CODE = '1'\". OGR SQL for file formats, SedonaDB SQL for GeoParquet. Recorded in the step.",
    ),
    keep_columns: str | None = typer.Option(
        None,
        help="Comma-separated attribute columns to keep (geometry and --name-field are always kept). Default: all.",
    ),
    name_field: str | None = typer.Option(
        None,
        help="Geometry sets: column with unique, non-blank names. Adds csdr-id and csdr-name.",
    ),
    geometry_id: str | None = typer.Option(
        None,
        help="Geometry sets: kebab-case seed for csdr-id. Defaults to the source file name. Needs --name-field.",
    ),
    partition: bool = typer.Option(
        False,
        "--partition/--no-partition",
        help="Also write partition/<slug>_<lon>_<lat>.parquet (EPSG:4326 grid), the geoparquet dataset layout process-geometry reads. If partition/ already holds this slug's files they are always rewritten after a write, with or without this flag (recorded as partition_rewritten). One dataset per target folder: another slug's partitions are refused.",
    ),
    grid_chunks: int = typer.Option(
        10,
        min=1,
        help="Partition grid: 2N longitude by N latitude cells (10 = 18 degree cells, 40 = 4.5 degree cells).",
    ),
    create_pmtiles: bool = typer.Option(
        False,
        "--create-pmtiles/--no-create-pmtiles",
        help="Also write <slug>.pmtiles with tippecanoe from an EPSG:4326 copy.",
    ),
    source_sha256: str | None = typer.Option(
        None,
        help="Expected SHA-256 (64 hex) of the source bytes. Verified before writing; the computed hash is always recorded.",
    ),
    layer_column_value: str | None = typer.Option(
        None,
        help="Add a 'layer' column with this constant, so several sources can share one dataset.",
    ),
    append: bool = typer.Option(
        False,
        "--append",
        help="Append to an existing <slug>.parquet (needs --layer-column-value). Existing rows with the same layer value are replaced and the file is rewritten. Existing partitions for this slug are always rewritten too (see --partition).",
    ),
    filter_geometry: str | None = typer.Option(
        None,
        help="Local path or store URL of a vector file; keep only source features intersecting the union of its features (reprojected to the source CRS). Recorded with its SHA-256.",
    ),
    clip: bool = typer.Option(
        False,
        "--clip",
        help="With --filter-geometry: also clip kept geometries to the union and repair them (polygonal parts only for polygons).",
    ),
    make_valid: bool = typer.Option(
        True,
        "--make-valid/--no-make-valid",
        help="Repair invalid geometries with shapely make_valid (structure method). Recorded as a method note.",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace an existing <slug>.parquet. Ignored with --append."
    ),
) -> None:
    """Convert a vector file to GeoParquet, optionally partitioned and tiled."""
    columns = parse_csv_list(keep_columns) if keep_columns else None
    result = convert_vector(
        source,
        target_location,
        target_filename=target_filename,
        zip_member=zip_member,
        layer=layer,
        where=where,
        keep_columns=columns,
        name_field=name_field,
        geometry_id=geometry_id,
        layer_column_value=layer_column_value,
        append=append,
        filter_geometry=filter_geometry,
        clip=clip,
        partition=partition,
        grid_chunks=grid_chunks,
        create_pmtiles=create_pmtiles,
        source_sha256=source_sha256,
        make_valid=make_valid,
        overwrite=overwrite,
    )
    if result.skipped:
        typer.echo(f"{result.target_url} exists and overwrite is off; skipped.")
    else:
        typer.echo(f"Wrote {result.row_count} features to {result.target_url}.")

    extras = []
    if partition:
        extras.append("partitioned")
    if create_pmtiles:
        extras.append("PMTiles")
    label = "Convert vector source to GeoParquet"
    if extras:
        label += f" ({', '.join(extras)})"
    write_step(
        label=label,
        inputs=step_inputs(
            result,
            layer=layer,
            where=where,
            keep_columns=columns,
            name_field=name_field,
            geometry_id=geometry_id,
            layer_column_value=layer_column_value,
            append=append or None,
            grid_chunks=grid_chunks if partition else None,
            make_valid=make_valid,
            method_notes=method_notes(result, make_valid) or None,
        ),
        outputs=step_outputs(result),
    )
