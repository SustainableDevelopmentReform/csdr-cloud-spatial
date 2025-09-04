from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import typer
from fiona.io import ZipMemoryFile
from loguru import logger
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import LocalStore, S3Store

from csdr.utils import exists

conversion_app = typer.Typer()


@conversion_app.command("zip-to-parquet")
def extract_gmw(
    source_zip_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to the zip file containing the geospatial data.",
        default="./cache/example.zip",
    ),
    source_internal_path_name: str = typer.Option(
        help="The internal path within the zip file to the data to extract.",
        default="example/example.shp",
    ),
    target_location: str = typer.Option(
        help="Local or remote path (file:// or s3://) to store the converted file.",
        default="./cache",
    ),
    overwrite: bool = typer.Option(
        True, help="Replace existing parquet file if it exists."
    ),
) -> None:
    logger.info("Starting parquet conversion process...")

    assert source_zip_location.endswith(".zip"), "Source file must be a .zip file"

    store = None
    source_zip_name_path = None
    if source_zip_location.startswith("s3://"):
        s3_url = urlparse(source_zip_location)
        bucket = s3_url.netloc
        store = S3Store(bucket, credential_provider=Boto3CredentialProvider())
        source_zip_name_path = s3_url.path.lstrip("/")
    else:
        store = LocalStore(prefix=Path(source_zip_location).parent, mkdir=True)
        source_zip_name_path = Path(source_zip_location).name

    source_exists = exists(store, source_zip_name_path)
    if not source_exists:
        logger.error(
            f"Source zip file does not exist at {source_zip_location}. Cannot extract."
        )
        raise typer.Exit(code=1)
    else:
        logger.info(
            f"Source zip file found at {source_zip_location}, proceeding with extraction."
        )

    # Set up the target store
    target_path = source_zip_name_path.replace(".zip", ".parquet")
    target_store = None
    full_destination_str = None

    if target_location.startswith("s3://"):
        target_s3_url = urlparse(target_location)
        bucket = target_s3_url.netloc
        target_store = S3Store(bucket, credential_provider=Boto3CredentialProvider())
        full_destination_str = f"s3://{bucket}/{target_path}"
    else:
        target_store = LocalStore(prefix=Path(target_location), mkdir=True)
        full_destination_str = str(Path(target_location) / target_path)

    # Check if target file already exists
    if exists(target_store, target_path) and not overwrite:
        logger.warning(
            f"Target parquet file already exists at {target_location}/{target_path}. Use --overwrite to replace."
        )
        raise typer.Exit(code=0)

    # Pull the whole zip into memory
    zip_bytes = BytesIO(store.get(source_zip_name_path).bytes())

    # Use Fiona's in-memory ZIP reader (works with bytes and includes all sidecar files)
    with ZipMemoryFile(zip_bytes) as z:
        # Open the shapefile within the ZIP
        with z.open(source_internal_path_name) as src:
            gdf = gpd.GeoDataFrame.from_features(src, crs=src.crs)

    logger.info(f"Loaded {len(gdf)} records from the shapefile.")

    # Write GeoDataFrame to a GeoParquet file in memory
    full_destination_str = None
    with BytesIO() as parquet_buffer:
        gdf.to_parquet(parquet_buffer, engine="pyarrow")
        parquet_buffer.seek(0)

        # Write the parquet bytes to the target store using obstore
        target_store.put(target_path, parquet_buffer.getvalue())
        if target_location.startswith("s3://"):
            full_destination_str = f"s3://{bucket}/{target_path}"
        else:
            full_destination_str = str(Path(target_location) / target_path)

    logger.info(
        f"Parquet extraction process completed. Wrote file to {full_destination_str}"
    )
