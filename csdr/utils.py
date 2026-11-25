import hashlib
import logging
import subprocess
import uuid
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pystac
from obstore.store import ObjectStore
from odc.geo.geobox import GeoBox
from odc.geo.geom import Geometry
from odc.geo.xr import rasterize
from odc.stac import load
from xarray import DataArray, Dataset

from csdr.io import exists, find_matching_files, write_gdf_to_parquet

logger = logging.getLogger(__name__)


class CSDRException(Exception):
    pass


# Source identity helpers shared by the conversion engines (vector_convert and
# any later one): a declared SHA-256 is normalised and verified, and the bytes
# actually read are hashed while they are staged.
SHA256_CHUNK = 8 * 1024 * 1024
_SHA256_HEX = frozenset("0123456789abcdef")


def normalise_sha256(value: str | None) -> str | None:
    """Return a lower-case 64-hex SHA-256 or refuse the value."""
    if value is None:
        return None
    digest = value.strip().lower()
    if len(digest) != 64 or not set(digest) <= _SHA256_HEX:
        raise CSDRException(
            "A source SHA-256 must be 64 hexadecimal characters (no 'sha256:' prefix)."
        )
    return digest


def verify_sha256(declared: str | None, actual: str) -> None:
    """Refuse a declared SHA-256 that differs from the bytes read."""
    if declared is not None and declared != actual:
        raise CSDRException(
            f"Source SHA-256 mismatch: declared {declared}, read {actual}. "
            "The source changed or the declaration is wrong; nothing was written."
        )


def sha256_file(path: Path) -> str:
    """SHA-256 of a local file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(SHA256_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_remote_url(source: str) -> bool:
    """True for store URLs (s3://, https://); False for local paths and file://."""
    return "://" in source and not source.lower().startswith("file://")


def local_file_path(source: str) -> Path:
    """A local path from a plain path or a file:// URL."""
    if source.lower().startswith("file://"):
        source = source[len("file://") :]
    return Path(source)


def url_suffix(name: str) -> str:
    """Lower-case final extension of a path or URL (``.tif``, ``.dbf``)."""
    return PurePosixPath(name).suffix.lower()


def download_with_sha256(store: ObjectStore, name: str, target: Path) -> str:
    """Stream one object to disk, returning its SHA-256."""
    digest = hashlib.sha256()
    result = store.get(name)
    with target.open("wb") as output:
        for chunk in result.stream(min_chunk_size=SHA256_CHUNK):
            output.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()


def run_command(command: list[str]) -> tuple[bool, str, str]:
    """Runs a shell command and returns success status, stdout, and stderr."""
    try:
        process = subprocess.run(
            command,
            check=False,  # Don't raise exception on non-zero exit
            capture_output=True,
            text=True,
            encoding="utf-8",  # Ensure consistent encoding
        )
        # Log stderr even on success, as it might contain warnings
        if process.stderr:
            cmd_str = " ".join(command)
            logger.debug(f"Command '{cmd_str}' stderr:")
            logger.debug(process.stderr.strip())

        if process.returncode == 0:
            return True, process.stdout.strip(), ""
        else:
            # Construct error message carefully to avoid f-string issues
            cmd_str = " ".join(command)
            stderr_str = process.stderr.strip()
            stdout_str = process.stdout.strip()
            error_message = (
                f"Command '{cmd_str}' failed with exit code {process.returncode}.\n"
                f"Stderr:\n{stderr_str}\n"
                f"Stdout:\n{stdout_str}"
            )
            logger.error(error_message)
            return False, stdout_str, stderr_str
    except FileNotFoundError:
        cmd_zero = command[0] if command else "<empty command>"
        logger.error(f"Command not found: {cmd_zero}")
        return False, "", f"Command not found: {cmd_zero}"
    except Exception as e:
        cmd_str = " ".join(command)
        logger.exception(f"Failed to run command '{cmd_str}'")
        return False, "", str(e)


def load_xarray_stacgeoparquet(
    items: pystac.ItemCollection,
    geometry: Geometry,
    **load_kwargs: dict[str, Any],
) -> Dataset:
    # Force the use of Dask. Redundant because it is already done in get_area_m2_from_dataset_geometry (parent function).
    if "chunks" not in load_kwargs:
        load_kwargs["chunks"] = {}

    # load_kwargs.resolution units must match CRS. We should check this. We are passing 10 (meters) for example but the units could be degrees if CRS is geographic.
    # ODC STAC load
    # geopolygon is essential for antimeridian-crossing COGs e.g. DEP Seagrass for Fiji. Without this a world-spanning dataset is loaded which causes OOM Killed errors.
    # geopolygon only limits to the BOUNDING BOX. So masking is still needed after loading.
    data = load(items, geopolygon=geometry, **load_kwargs)
    assert data.sizes["x"] < 1_000_000, (
        "Error: Loaded data's X dimension spans the whole world (at 10m resolution)."
    )

    return data


def raster_boundary_mask(
    geobox: GeoBox, geometry_4326: Geometry, all_touched: bool = True
) -> np.ndarray:
    """Cells of ``geobox`` inside the boundary, as a 2-D boolean array.

    This is the rasterisation every raster measurement uses: the boundary goes
    from EPSG:4326 to EPSG:6933 and then to the grid's CRS, and is burned with
    ``all_touched`` (every touched cell) or the cell-centre rule.
    """
    geom_6933 = geometry_4326.to_crs("EPSG:6933")
    return rasterize(geom_6933, geobox, all_touched=all_touched).data


def raster_valid_mask(
    data: DataArray | np.ndarray, nodata: float | None
) -> DataArray | np.ndarray:
    """Finite cells that are not the nodata value (same type as ``data``)."""
    valid = np.isfinite(data)
    if nodata is not None:
        valid = valid & (data != nodata)
    return valid


def xarray_calculate_area_m2(
    data: Dataset | DataArray,
    geometry_4326: Geometry,
    indicator: str | None = None,
    value_list: list[float] | None = None,
    all_touched: bool = True,
) -> float:
    """Area in m² of the valid cells inside the boundary (of the selected values).

    A cell is valid when it is finite and not the band's nodata value; the
    value selection applies to valid cells only, so a nodata value is never
    counted. ``all_touched`` burns every cell the boundary touches (the
    default); ``False`` selects cells by their centres.

    With no valid cell inside the boundary the area is reported as 0.0 with a
    warning, as it always has been for sum-<x>-area. The zonal statistics
    families share the masks below but refuse a boundary without valid cells
    (``ZonalCells.require_support``), because a mean of no cells is undefined.
    """
    # Work with a dataarray, not a dataset, so it's a singular thing
    if type(data) is not DataArray:
        if indicator is None:
            raise CSDRException("Indicator must be specified when data is a Dataset.")
        data = data[indicator]

    # Decide which cells are valid before selecting values, so a declared
    # nodata value is never counted as data.
    inside = raster_boundary_mask(data.odc.geobox, geometry_4326, all_touched)
    valid = raster_valid_mask(data, data.odc.nodata) & inside
    selected = valid if value_list is None else valid & data.isin(value_list)

    # Compute both counts together so a lazy source need not be read twice.
    counts = Dataset(
        {"valid_cells": valid.sum(), "selected_cells": selected.sum()}
    ).compute()
    if counts["valid_cells"].item() == 0:
        logger.warning(
            "No valid raster cells cover the requested geometry; reporting an area "
            "of 0.0. Check the source coverage and reporting period."
        )
        return 0.0

    count = float(counts["selected_cells"].item())
    one_pixel_area_m2 = abs(data.odc.geobox.resolution.x * data.odc.geobox.resolution.y)

    return round(float(count) * one_pixel_area_m2, 2)


# Make a UUID
def make_uuid(thing: str) -> str:
    namespace = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial",
    )
    return str(uuid.uuid5(namespace, thing))


@contextmanager
def suppress_rust_output() -> Generator[None, None, None]:
    """Context manager to suppress all output from Rust code using OS-level redirection."""
    import os

    # Save original file descriptors
    stdout_fd = os.dup(1)
    stderr_fd = os.dup(2)

    try:
        # Redirect stdout and stderr to /dev/null (Unix) or NUL (Windows)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)  # Redirect stdout
        os.dup2(devnull, 2)  # Redirect stderr
        os.close(devnull)

        yield

    finally:
        # Restore original file descriptors
        os.dup2(stdout_fd, 1)
        os.dup2(stderr_fd, 2)
        os.close(stdout_fd)
        os.close(stderr_fd)


def get_geom_from_gdf(gdf: gpd.GeoDataFrame, geometry_id: str) -> Geometry:
    features = gdf[gdf["csdr-id"] == geometry_id]
    if len(features) == 0:
        raise CSDRException(f"Geometry ID {geometry_id} not found in GeoDataFrame.")
    if len(features) > 1:
        raise CSDRException(f"Geometry ID {geometry_id} is not unique in GeoDataFrame.")
    feature = features.iloc[0]

    # Convert to ODC geometry
    return Geometry(feature.geometry, crs=gdf.crs)


PARTITION_PREFIX = "partition/"
_PARTITION_HELPER_COLUMNS = ["lon", "lat", "lon_bin", "lat_bin", "partition"]


def partition_parquet(
    target_store: ObjectStore,
    gdf: gpd.GeoDataFrame,
    grid_chunks: int = 10,
    overwrite: bool = True,
    file_prefix: str = "part",
) -> int:
    """Partition a GeoDataFrame into ``partition/<file_prefix>_<lon>_<lat>.parquet``.

    This is the ``geoparquet`` dataset contract the product engine reads
    (``<dir>/<name>.parquet`` plus ``<dir>/partition/*.parquet``). Features are
    assigned to a global grid by centroid: ``grid_chunks * 2`` longitude cells by
    ``grid_chunks`` latitude cells (the default 10 gives 18 degree cells).

    Partition files are always written in EPSG:4326, because the product engine
    intersects them with EPSG:4326 boundaries. A source in another CRS is
    reprojected for the partitions only; the caller keeps its native-CRS file.
    The caller's GeoDataFrame is not modified. Returns the number of partition
    files written.
    """
    if grid_chunks < 1:
        raise CSDRException("grid_chunks must be at least 1.")
    if gdf.crs is None:
        raise CSDRException(
            "Cannot partition a GeoDataFrame without a CRS. Set the source CRS first."
        )
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    else:
        gdf = gdf.copy()

    # Number of edges is one more than number of intervals.
    lon_edges = np.linspace(-180, 180, grid_chunks * 2 + 1)
    lat_edges = np.linspace(-90, 90, grid_chunks + 1)
    # Centroids in degrees only choose a grid cell; their small geographic error
    # does not matter for partitioning, so the geographic-CRS warning is muted.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Geometry is in a geographic CRS")
        centroids = gdf.geometry.centroid
    gdf["lon"] = centroids.x
    gdf["lat"] = centroids.y
    gdf["lon_bin"] = pd.cut(gdf["lon"], lon_edges, labels=False, include_lowest=True)
    gdf["lat_bin"] = pd.cut(gdf["lat"], lat_edges, labels=False, include_lowest=True)
    # Null or empty geometries have no centroid and no cell; they are not
    # partitioned. Bins are integers so file names never become floats.
    binned = gdf["lon_bin"].notna() & gdf["lat_bin"].notna()
    if not binned.all():
        logger.warning(
            f"{int((~binned).sum())} features without a centroid (null or empty "
            "geometry) are not partitioned."
        )
        gdf = gdf[binned].copy()
    gdf["lon_bin"] = gdf["lon_bin"].astype(int)
    gdf["lat_bin"] = gdf["lat_bin"].astype(int)
    gdf["partition"] = gdf["lon_bin"].astype(str) + "_" + gdf["lat_bin"].astype(str)

    # With overwrite on, delete every existing partition file first. Otherwise a
    # changed grid would leave duplicate features in two partition layouts.
    if overwrite:
        partition_files = find_matching_files(
            target_store, prefix=PARTITION_PREFIX, pattern=r"\.parquet$"
        )
        logger.info(f"Deleting {len(partition_files)} existing partition files ...")
        for file in partition_files:
            target_store.delete(file)
    else:
        logger.info("Overwrite is off, existing partition files will be preserved ...")

    written = 0
    for partition, group in gdf.groupby("partition"):
        file_name = f"{PARTITION_PREFIX}{file_prefix}_{partition}.parquet"
        if exists(target_store, file_name) and not overwrite:
            logger.info(
                f"Skipping partition {partition}, already exists and overwrite is off."
            )
            continue
        write_gdf_to_parquet(
            group.drop(_PARTITION_HELPER_COLUMNS, axis=1), target_store, file_name
        )
        written += 1
    logger.info(f"Wrote {written} partition files.")
    return written
