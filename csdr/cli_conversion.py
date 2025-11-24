import os
import subprocess
from io import BytesIO
from tempfile import TemporaryDirectory

import geopandas as gpd
import typer
from fiona.io import ZipMemoryFile
import logging

from csdr.geometries import add_geometry_id_name
from csdr.io import (
    exists,
    get_prefix_file_name_from_url,
    get_store_from_url,
    make_url_from_store_prefix_filename,
    read_geospatial_file,
    write_gdf_to_parquet,
)

conversion_app = typer.Typer()


def _get_geometry_id(geometry_id: str | None, dataset_url: str) -> str | None:
    if geometry_id is None:
        geometry_id = (
            get_prefix_file_name_from_url(dataset_url).replace(" ", "-").lower().split(".")[0]
        )
    return geometry_id


@conversion_app.command("zip-to-parquet")
def convert_zipfile_to_parquet(
    source_zip_location: str = typer.Option(
        help="Local or remote path (local or s3://) to the zip file containing the geospatial data.",
        default="./cache/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip", # EEZ is just an example
    ),
    source_internal_path_name: str = typer.Option(
        help="The internal path within the zip file to the data to extract.",
        default="EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp", # EEZ is just an example
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

    store = get_store_from_url(source_zip_location)
    source_zip_name_path = get_prefix_file_name_from_url(source_zip_location)

    if not exists(store, source_zip_name_path):
        logging.error(
            f"Source zip file does not exist at {source_zip_location}. Cannot extract."
        )
        raise typer.Exit(code=1)
    else:
        logging.info(
            f"Source zip file found at {source_zip_location}, proceeding with extraction."
        )

    target_location = target_location.rstrip("/")

    # Set up the target store
    target_store = get_store_from_url(target_location)
    target_filename = source_internal_path_name.split("/")[-1].replace(
        ".shp", ".parquet"
    )
    
    target_url = make_url_from_store_prefix_filename(target_store, target_filename)

    # Check if target file already exists
    if exists(target_store, target_filename) and not overwrite:
        logging.warning(
            f"Target parquet file already exists at {target_url}. Use --overwrite to replace."
        )
        raise typer.Exit(code=0)

    # Pull the whole zip into memory
    zip_bytes = BytesIO(store.get(source_zip_name_path).bytes())

    # Use Fiona's in-memory ZIP reader (works with bytes and includes all sidecar files)
    with ZipMemoryFile(zip_bytes) as z:
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

    store = get_store_from_url(source_location)
    source_name_path = get_prefix_file_name_from_url(source_location)

    if not exists(store, source_name_path):
        logging.error(
            f"Source geospatial file does not exist at {source_location}. Cannot convert."
        )
        raise typer.Exit(code=1)
    else:
        logging.info(
            f"Source geospatial file found at {source_location}, proceeding with conversion."
        )
    if target_location is None:
        target_location = source_location

    # Set up the target store
    target_store = get_store_from_url(target_location)
    target_filename = source_name_path.split("/")[-1].rsplit(".", 1)[0] + ".parquet"
    target_url = make_url_from_store_prefix_filename(target_store, target_filename)

    # Check if target file already exists
    if exists(target_store, target_filename) and not overwrite:
        logging.warning(
            f"Target parquet file already exists at {target_url}. Use --overwrite to replace."
        )
        raise typer.Exit(code=0)

    # Read the geospatial file into a GeoDataFrame
    gdf = read_geospatial_file(source_location)

    gdf = add_geometry_id_name(
        gdf, name_field=name_field, geometry_id=_get_geometry_id(None, source_name_path)
    )

    logging.info(f"Opened file with {len(gdf)} features")

    with BytesIO() as parquet_buffer:
        gdf.to_parquet(parquet_buffer, engine="pyarrow")
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        target_store.put(target_filename, parquet_buffer.getvalue())

    logging.info(f"Loaded {len(gdf)} records from the geospatial file.")
    logging.info(f"Parquet conversion process completed. Wrote file to {target_url}")
