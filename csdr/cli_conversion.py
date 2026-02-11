import logging
import os
import subprocess
from io import BytesIO
from tempfile import TemporaryDirectory

import geopandas as gpd
import typer
from fiona.io import ZipMemoryFile
from odc.geo import MultiPolygon, Polygon

from csdr.geometries import add_geometry_id_name
from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    read_geospatial_file,
    split_path_and_file_name_from_url,
    write_gdf_to_parquet,
)
from csdr.utils import CSDRException

conversion_app = typer.Typer()


def _get_geometry_id(geometry_id: str | None, dataset_url: str) -> str | None:
    if geometry_id is None:
        geometry_id = (
            # Get the file name, replace spaces with dashes, lowercase, and remove extension
            split_path_and_file_name_from_url(dataset_url)[1]
            .replace(" ", "-")
            .lower()
            .split(".")[0]
        )
    return geometry_id


def _pre_convert_validation(gdf: gpd.GeoDataFrame) -> None:
    """
    Validate an input geodataframe.
    Rules:
    1. Has geometry of polygon/multipolygon.
    2. Area < area_maximum_limit (e.g. 650,000 sq km, approx. size of France).
    We already checked file size in an earlier step. Do we also want to check geometry complexity? Something like vertex count.
    """
    if gdf.empty or len(gdf) == 0:
        raise CSDRException("The GeoParquet file is empty.")
    # TODO: Finalise limitations.
    area_maximum_limit_m2 = 650_000  # Approx. size of France.

    # Fail if no geometry column
    if "geometry" not in gdf.columns:
        raise CSDRException("No geometry column found in the GeoParquet file.")

    if gdf["geometry"].is_empty.any() or gdf["geometry"].isna().any():
        raise CSDRException("Empty geometries are not allowed.")

    if gdf["geometry"].is_valid.all() is False:
        raise CSDRException("All geometries must be valid according to OGC standards.")

    if gdf["geometry"].geom_type.isin(["Polygon", "MultiPolygon"]).all() is False:
        raise CSDRException(
            f"All geometries must be of type Polygon or MultiPolygon, not {gdf['geometry'].geom_type}"
        )

    gdf["geometry_6933"] = gdf["geometry"].to_crs(
        "EPSG:6933"
    )  # Use equal-area projection for accurate area calculation
    parquet_geometry_area = gdf["geometry_6933"].area.sum()
    if parquet_geometry_area > area_maximum_limit_m2:
        raise CSDRException(
            f"Total geometry area {parquet_geometry_area} m^2 exceeds maximum limit of {area_maximum_limit_m2} m^2."
        )

    # TODO: Consider adding a check for vertex count or geometry complexity here, to avoid trying to convert geometries that are too complex and will fail in tippecanoe or in the API. This could be a simple check on the total number of vertices across all geometries, with some maximum limit.
    vertex_maximum_per_feature = 10_000  # Example limit, adjust as needed

    def count_vertices(geom: Polygon | MultiPolygon) -> int:
        if geom.geom_type == "Polygon":
            return len(geom.exterior.coords)
        elif geom.geom_type == "MultiPolygon":
            return sum(len(part.exterior.coords) for part in geom.geoms)
        raise CSDRException(
            f"Unsupported geometry type {geom.geom_type} for vertex counting."
        )

    vertex_counts = gdf["geometry"].apply(count_vertices)
    exceeding = vertex_counts[vertex_counts > vertex_maximum_per_feature]
    if not exceeding.empty:
        idx = exceeding.index[0]
        count = exceeding.iloc[0]
        raise CSDRException(
            f"Feature at index {idx} has {count} vertices, exceeding the per-feature maximum of {vertex_maximum_per_feature}."
        )

    logging.info("Pre-convert validation passed.")


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
    logging.info("Starting parquet conversion process...")

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
    logging.info(
        f"Source zip file found at {source_zip_location}, proceeding with extraction."
    )

    target_location = target_location.rstrip("/")

    # Set up the target store
    target_store = get_store_with_prefix_from_url(target_location)
    target_filename = source_internal_path_name.split("/")[-1].replace(
        ".shp", ".parquet"
    )
    target_url = f"{target_location}/{target_filename}"

    # Check if target file already exists
    if exists(target_store, target_filename) and not overwrite:
        logging.info(
            f"Target parquet file already exists at {target_url} and overwrite is off. Use --overwrite to replace. Exiting successfully."
        )
        raise typer.Exit(code=0)  # Exit successfully, nothing to do
    logging.info(
        "Target parquet file does not exist or overwrite is on, proceeding with extraction."
    )

    # Pull the whole zip into memory
    zip_bytes = BytesIO(source_store.get(source_zip_name).bytes())
    zip_bytes.seek(0)  # Ensure pointer is at start

    with ZipMemoryFile(zip_bytes) as z:
        files_in_zip = z.listdir()
        logging.info(f"Files in zip: {files_in_zip}")
        logging.info(f"Requested internal path: {source_internal_path_name}")
        if source_internal_path_name not in files_in_zip:
            raise CSDRException(
                f"Internal path {source_internal_path_name} not found in zip file."
            )
        # Open the shapefile within the ZIP
        with z.open(source_internal_path_name) as src:
            gdf = gpd.GeoDataFrame.from_features(src, crs=src.crs)

    logging.info(f"Loaded {len(gdf)} records from the shapefile.")

    # Add ID and Name fields
    gdf = add_geometry_id_name(
        gdf,
        name_field=name_field,
        geometry_id=_get_geometry_id(geometry_id, source_internal_path_name),
    )

    # TODO: See if we can do any pre-validation before writing.
    _pre_convert_validation(source_zip_location)

    write_gdf_to_parquet(gdf, target_store, target_filename)

    # !tippecanoe --force -z 10 --no-simplification-of-shared-nodes
    # --simplification 10 --drop-densest-as-needed
    # -l "data" -o ../geometries/acsc-ga-2015/out/acsc-primary-compartments.pmtiles ../geometries/acsc-ga-2015/out/acsc-primary-compartments.geojson

    if create_pmtiles:
        logging.info("Creating PMTiles file alongside the parquet...")
        # Create a PMTiles files with tippecanoe
        pmtiles_file = target_filename.replace(".parquet", ".pmtiles")

        # Do the work in a local temp directory
        with TemporaryDirectory() as tmpdirname:
            local_geojson = os.path.join(tmpdirname, "data.geojson")
            local_pmtiles = os.path.join(tmpdirname, "data.pmtiles")

            # Keep only the id and name fields, plus geometry
            gdf = gdf[["csdr-id", "csdr-name", "geometry"]]
            gdf.to_file(local_geojson, driver="GeoJSON")

            # Create PMTiles file with tippecanoe
            subprocess.run(
                [
                    "tippecanoe",
                    "--force",
                    "-z",
                    "10",
                    "--no-simplification-of-shared-nodes",
                    "--simplification",
                    "10",
                    "--drop-densest-as-needed",
                    "--layer",
                    "data",
                    "--output",
                    local_pmtiles,
                    local_geojson,
                ],
                check=True,
            )

            # Upload the PMTiles file to the target store
            target_store.put(pmtiles_file, local_pmtiles)
        logging.info(f"Created PMTiles file at {pmtiles_file}")
    else:
        logging.info("Skipping PMTiles creation because flag is set to false.")

    logging.info(f"Parquet extraction process completed. Wrote file to {target_url}")


@conversion_app.command("geo-to-parquet")
def convert_geospatial_file_to_parquet(
    source_location: str = typer.Option(
        help="Local or remote path (local or s3://) to the geospatial file.",
        default="./tests/data/single_geometry.geojson",
    ),
    target_location: str | None = typer.Option(
        help="Local or remote path (local or s3://) to store the converted file.",
        default=None,
    ),
    name_field: str = typer.Option(
        "name", help="The field in the data to use for the 'Name' attribute."
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing parquet file if it exists."
    ),
) -> None:
    logging.info("Starting geospatial to parquet conversion process...")

    source_path, source_name = split_path_and_file_name_from_url(source_location)
    source_store = get_store_with_prefix_from_url(source_path)

    if not exists(source_store, source_name):
        raise CSDRException(
            f"Source geospatial file does not exist at {source_location}. Cannot convert."
        )
    else:
        logging.info(
            f"Source geospatial file found at {source_location}, proceeding with conversion."
        )
    if target_location is None:
        target_location = source_location

    target_location = target_location.rstrip("/")

    # Set up the target store
    target_store = get_store_with_prefix_from_url(target_location)
    target_filename = source_name.rsplit(".", 1)[0] + ".parquet"

    target_url = f"{target_location}/{target_filename}"

    # Check if target file already exists
    if exists(target_store, target_filename) and not overwrite:
        logging.warning(
            f"Target parquet file already exists at {target_url}. Use --overwrite to replace."
        )
        raise typer.Exit(code=0)  # Exit successfully, nothing to do

    # Read the geospatial file into a GeoDataFrame
    gdf = read_geospatial_file(source_location)

    gdf = add_geometry_id_name(
        gdf, name_field=name_field, geometry_id=_get_geometry_id(None, source_name)
    )

    logging.info(f"Opened file with {len(gdf)} features")

    with BytesIO() as parquet_buffer:
        gdf.to_parquet(parquet_buffer, engine="pyarrow")
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        target_store.put(target_filename, parquet_buffer.getvalue())

    logging.info(f"Loaded {len(gdf)} records from the geospatial file.")
    logging.info(f"Parquet conversion process completed. Wrote file to {target_url}")
