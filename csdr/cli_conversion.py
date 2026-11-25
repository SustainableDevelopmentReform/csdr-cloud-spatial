import json
import logging
from pathlib import PurePosixPath

import typer

from csdr import raster_stac
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
        count_invalid=False,
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
            # Also when existing partitions were rewritten without --partition.
            grid_chunks=grid_chunks if result.partition_count is not None else None,
            make_valid=make_valid,
            method_notes=method_notes(result, make_valid) or None,
        ),
        outputs=step_outputs(result),
    )


@conversion_app.command("raster-to-stac")
def convert_raster_to_stac(
    source: str = typer.Option(
        ..., help="Local path or store URL (s3://, https://) of a GeoTIFF (.tif/.tiff)."
    ),
    target_location: str = typer.Option(
        ...,
        help="Local or s3:// folder for cogs/, items/ and collection.json. Local paths are made absolute so STAC hrefs resolve.",
    ),
    asset_name: str = typer.Option(
        ...,
        help="STAC asset key, i.e. the band name process-geometry selects with indicator-name.",
    ),
    datetime_value: str = typer.Option(
        ...,
        "--datetime",
        help="ISO 8601 date or datetime for every item, e.g. 2023-01-01 (UTC when no offset; a bare year means 1 January).",
    ),
    collection: str | None = typer.Option(
        None, help="STAC collection id. Defaults to the output slug."
    ),
    target_filename: str | None = typer.Option(
        None,
        help="Output slug for COG, item and display names. Defaults to the source file name without extension.",
    ),
    band: int = typer.Option(1, min=1, help="Source band to convert (1-based)."),
    tile_size: int | None = typer.Option(
        None,
        min=1,
        help="Tile edge in pixels: square windows aligned to the source grid from its top-left pixel. Default: one item for the whole raster.",
    ),
    tile_degrees: float | None = typer.Option(
        None,
        help="Convenience for --tile-size: tile edge in degrees, converted to whole pixels at the raster centre (approximate for a projected CRS; the resolved size is recorded).",
    ),
    classification_table: str | None = typer.Option(
        None,
        help="Class table for classification:classes on the asset: .dbf (e.g. a .vat.dbf), .csv, .json list of {value, name, ...} or GDAL .aux.xml raster attribute table.",
    ),
    class_value_column: str = typer.Option(
        "Value",
        help="Classification table column with the raster cell value (matched case-insensitively if absent).",
    ),
    class_name_column: str | None = typer.Option(
        None,
        help="Classification table column with the class label. Default: the first of name, label, class, title, then the first column ending in 'name'.",
    ),
    class_extra_columns: str | None = typer.Option(
        None,
        help="Comma-separated classification table columns copied into each class entry (e.g. grouping fields).",
    ),
    display_copy: str | None = typer.Option(
        None,
        help="Local or s3:// folder for <slug>_display.tif, one coarser COG of the whole raster for map display (source CRS, or --display-crs).",
    ),
    display_resolution: float | None = typer.Option(
        None,
        help="Display copy pixel size in source CRS units (display CRS units with --display-crs). Default: the coarser of 250 m (or its degree equivalent) and 10x the source resolution.",
    ),
    display_resampling: str | None = typer.Option(
        None,
        help="Display copy resampling (GDAL name). Default: mode for integer data, average for floating point.",
    ),
    display_crs: str | None = typer.Option(
        None,
        help="Reproject the display copy to this CRS (e.g. EPSG:3857); --display-resolution is then in its units. Default: the source CRS. The analysis COGs are never warped.",
    ),
    nodata: float | None = typer.Option(
        None,
        help="Nodata value for the COGs. Sets one when the source has none; replacing a source value is recorded as a method note.",
    ),
    source_sha256: str | None = typer.Option(
        None,
        help="Expected SHA-256 (64 hex) of the source bytes. Verified before writing; the computed hash is always recorded.",
    ),
    item_properties: str | None = typer.Option(
        None,
        help='JSON object merged into every item\'s properties, e.g. \'{"epoch": "2020-21"}\'.',
    ),
    overview_resampling: str | None = typer.Option(
        None,
        help="COG overview resampling (GDAL name). Default: mode for integer data, average for floating point.",
    ),
    num_threads: str = typer.Option(
        "ALL_CPUS", help="GDAL NUM_THREADS for COG compression and overviews."
    ),
    overwrite: bool = typer.Option(
        True,
        help="Rewrite existing COGs and items. Items of this slug and collection that the run did not write (an earlier tiling) are removed. With --no-overwrite, tiles whose COG and item exist are kept (resume) and earlier items are kept with a warning.",
    ),
) -> None:
    """Convert one raster band to COGs indexed by STAC items, without warping."""
    properties = None
    if item_properties is not None:
        try:
            properties = json.loads(item_properties)
        except json.JSONDecodeError as error:
            raise CSDRException(f"--item-properties is not JSON: {error}") from None
        if not isinstance(properties, dict):
            raise CSDRException("--item-properties must be a JSON object.")
    extra_columns = parse_csv_list(class_extra_columns) if class_extra_columns else None
    result = raster_stac.convert_raster(
        source,
        target_location,
        asset_name=asset_name,
        item_datetime=raster_stac.parse_datetime(datetime_value),
        collection_id=collection,
        target_filename=target_filename,
        band=band,
        tile_size=tile_size,
        tile_degrees=tile_degrees,
        classification_table=classification_table,
        class_value_column=class_value_column,
        class_name_column=class_name_column,
        class_extra_columns=extra_columns,
        display_copy=display_copy,
        display_resolution=display_resolution,
        display_resampling=display_resampling,
        display_crs=display_crs,
        nodata=nodata,
        source_sha256=source_sha256,
        item_properties=properties,
        overview_resampling=overview_resampling,
        num_threads=num_threads,
        overwrite=overwrite,
    )
    typer.echo(
        f"Wrote {len(result.item_urls)} COGs and STAC items to {result.target_location} "
        f"({result.skipped_all_nodata} all-nodata tiles skipped)."
    )
    write_step(
        label=f"Convert raster band to {len(result.cog_urls)} COGs indexed by STAC items",
        inputs=raster_stac.step_inputs(
            result,
            band=band,
            asset_name=asset_name,
            datetime=datetime_value,
            collection=result.collection_id,
            class_value_column=class_value_column
            if classification_table is not None
            else None,
            class_name_column=class_name_column,
            class_extra_columns=extra_columns,
            item_properties=properties,
            overview_resampling=overview_resampling,
            display_resampling=display_resampling,
            overwrite=overwrite,
            method_notes=raster_stac.method_notes(result),
        ),
        outputs=raster_stac.step_outputs(result),
    )
