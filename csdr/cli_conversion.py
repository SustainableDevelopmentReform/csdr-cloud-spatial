import logging
import os
import subprocess
from io import BytesIO
from tempfile import TemporaryDirectory

import geopandas as gpd
import typer
from fiona.io import ZipMemoryFile

from csdr.geometries import add_geometry_id_name
from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    split_path_and_file_name_from_url,
    write_gdf_to_parquet,
)
from csdr.provenance import write_step
from csdr.utils import CSDRException

conversion_app = typer.Typer()
logger = logging.getLogger(__name__)


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

    # Set up the target store
    target_store = get_store_with_prefix_from_url(target_location)
    target_filename = source_internal_path_name.split("/")[-1].replace(
        ".shp", ".parquet"
    )
    target_url = f"{target_location}/{target_filename}"

    # Check if target file already exists
    if exists(target_store, target_filename) and not overwrite:
        logger.info(
            f"Target parquet file already exists at {target_url} and overwrite is off. Use --overwrite to replace. Exiting successfully."
        )
    else:
        logger.info(
            "Target parquet file does not exist or overwrite is on, proceeding with extraction."
        )

        # Pull the whole zip into memory
        zip_bytes = BytesIO(source_store.get(source_zip_name).bytes())
        zip_bytes.seek(0)  # Ensure pointer is at start

        with ZipMemoryFile(zip_bytes) as z:
            files_in_zip = z.listdir()
            logger.info(f"Files in zip: {files_in_zip}")
            logger.info(f"Requested internal path: {source_internal_path_name}")
            if source_internal_path_name not in files_in_zip:
                raise CSDRException(
                    f"Internal path {source_internal_path_name} not found in zip file."
                )
            # Open the shapefile within the ZIP
            with z.open(source_internal_path_name) as src:
                gdf = gpd.GeoDataFrame.from_features(src, crs=src.crs)

        logger.info(f"Loaded {len(gdf)} records from the shapefile.")

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
            logger.info("Creating PMTiles file alongside the parquet...")
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
            logger.info(f"Created PMTiles file at {pmtiles_file}")
        else:
            logger.info("Skipping PMTiles creation because flag is set to false.")

        logger.info(f"Parquet extraction process completed. Wrote file to {target_url}")

    # Write step regardless of whether we skipped or did the work.
    write_step(
        label=f"Convert zipped shapefile to GeoParquet{' and PMTiles' if create_pmtiles else ''}",
        inputs={
            "source_zip_location": source_zip_location,
            "source_internal_path_name": source_internal_path_name,
        },
        outputs={"target_url": target_url},
    )
