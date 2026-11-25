import hashlib
import logging
import subprocess
import uuid
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import dask
import dask.array
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


class NoValidRasterCellsError(CSDRException):
    """No raster cell inside the boundary is valid, so a statistic is undefined.

    Raised by the zonal statistics families only; sum-<x>-area reports such a
    boundary as 0.0 with a warning.
    """


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


def raster_extent_mask(geobox: GeoBox, extent: Geometry) -> np.ndarray | None:
    """Cells of ``geobox`` whose centres lie inside the source raster extent.

    ``None`` means every cell is inside, so no mask is needed. Loaded cells
    outside every source raster hold the fill value, which looks like nodata;
    this mask tells them apart from nodata cells inside the source.
    """
    extent = extent.to_crs(geobox.crs)
    if extent.contains(geobox.extent):
        return None
    return rasterize(extent, geobox, all_touched=False).data


def raster_valid_mask(
    data: DataArray | np.ndarray | dask.array.Array, nodata: float | None
) -> DataArray | np.ndarray | dask.array.Array:
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


PERCENTILE_METHODS = ("linear", "histogram")


@dataclass(frozen=True)
class PercentileMethod:
    """How p10/p50/p90 are computed from the valid cells.

    ``linear``: numpy linear interpolation over the valid values.
    ``histogram``: fixed-width bins over ``value_range``; a value is put in bin
    ``clip(int((v - lo) / (hi - lo) * bins), 0, bins - 1)`` (values outside the
    range fall into the end bins) and the quantile ``q`` is the midpoint of the
    first bin whose cumulative count reaches ``q * N``, i.e. the bin holding the
    ``ceil(q * N)``-th smallest valid value.
    """

    method: str = "linear"
    bins: int = 1000
    value_range: tuple[float, float] = (0.0, 1.0)

    def __post_init__(self) -> None:
        if self.method not in PERCENTILE_METHODS:
            raise CSDRException(
                f"Unknown percentile method '{self.method}'. Use one of {list(PERCENTILE_METHODS)}."
            )
        if self.bins < 1:
            raise CSDRException("The percentile histogram needs at least one bin.")
        lo, hi = self.value_range
        if not (np.isfinite(lo) and np.isfinite(hi) and lo < hi):
            raise CSDRException(
                f"The percentile range must be two finite numbers 'lo,hi' with lo < hi; got {self.value_range}."
            )

    @staticmethod
    def parse_range(text: str) -> tuple[float, float]:
        parts = [part.strip() for part in text.split(",")]
        try:
            lo, hi = (float(part) for part in parts)
        except ValueError:
            raise CSDRException(
                f"The percentile range must be 'lo,hi' (two numbers); got '{text}'."
            ) from None
        return lo, hi

    @property
    def is_histogram(self) -> bool:
        return self.method == "histogram"

    def metadata(self) -> dict[str, Any]:
        """Provenance keys for the per-geometry output metadata."""
        if not self.is_histogram:
            return {"percentileMethod": self.method}
        return {
            "percentileMethod": self.method,
            "percentileBins": self.bins,
            "percentileRange": [float(v) for v in self.value_range],
        }


@dataclass
class ZonalCells:
    """The cell populations of one raster band inside one boundary.

    ``boundary_cells`` counts cells inside the boundary and the source raster
    extent, nodata included; ``valid_cells`` the finite, non-nodata ones among
    them. The optional parts (values, histogram, distinct values) are filled
    only when a requested statistic needs them. ``merge`` combines the parts of
    a multipart geometry into one population (union, not a mean of parts).
    """

    boundary_cells: int = 0
    valid_cells: int = 0
    valid_sum: float = 0.0
    values: list[np.ndarray] | None = None
    histogram: np.ndarray | None = None
    histogram_clipped_cells: int = 0
    distinct: np.ndarray | None = None
    histogram_method: PercentileMethod | None = field(default=None, repr=False)
    # Where the source extent came from: "proj", "footprint" or "none" (None
    # when no extent was looked up). Merging keeps the weakest source.
    extent_source: str | None = None

    def merge(self, other: "ZonalCells") -> None:
        self.boundary_cells += other.boundary_cells
        self.valid_cells += other.valid_cells
        self.valid_sum += other.valid_sum
        if other.values is not None:
            self.values = (self.values or []) + other.values
        if other.histogram is not None:
            self.histogram = (
                other.histogram.copy()
                if self.histogram is None
                else self.histogram + other.histogram
            )
            self.histogram_method = other.histogram_method
        self.histogram_clipped_cells += other.histogram_clipped_cells
        self.extent_source = _weakest_extent_source(
            self.extent_source, other.extent_source
        )
        if other.distinct is not None:
            self.distinct = (
                other.distinct
                if self.distinct is None
                else np.union1d(self.distinct, other.distinct)
            )

    def require_support(self) -> None:
        # Zonal statistics refuse a boundary without valid cells: a mean or a
        # percentile of no cells is undefined, and 0 would look like a value.
        # (sum-<x>-area, an older family, keeps reporting 0.0 with a warning.)
        if self.valid_cells == 0:
            raise NoValidRasterCellsError(
                "No valid raster cells cover the requested geometry. "
                "Check the source coverage and reporting period before processing. "
                "Missing source coverage cannot be reported as zero, and statistics "
                "of no cells are undefined."
            )

    def mean(self) -> float:
        self.require_support()
        return float(self.valid_sum / self.valid_cells)

    def valid_fraction(self) -> float:
        self.require_support()
        return float(self.valid_cells / self.boundary_cells)

    def percentile(self, q: float, method: PercentileMethod) -> float:
        self.require_support()
        if method.is_histogram:
            if self.histogram is None or self.histogram_method != method:
                raise CSDRException(
                    "Histogram percentiles need the histogram from the same pass."
                )
            lo, hi = method.value_range
            cumulative = self.histogram[: method.bins].cumsum()
            target = cumulative[-1] * q
            index = int((cumulative < target).sum())
            index = min(max(index, 0), method.bins - 1)
            width = (hi - lo) / method.bins
            return float(lo + (index + 0.5) * width)
        if self.values is None:
            raise CSDRException("Linear percentiles need the valid values.")
        values = np.concatenate(self.values).astype(np.float64)
        return float(np.quantile(values, q, method="linear"))

    def distinct_count(self, value_list: list[float] | None = None) -> int:
        self.require_support()
        if self.distinct is None:
            raise CSDRException("Distinct counts need the distinct values.")
        if value_list is None:
            return int(self.distinct.size)
        return int(
            np.isin(self.distinct, np.asarray(value_list, dtype="float64")).sum()
        )

    def cell_counts(self) -> dict[str, int | str]:
        counts: dict[str, int | str] = {
            "boundaryCells": self.boundary_cells,
            "validCells": self.valid_cells,
        }
        if self.histogram is not None:
            counts["histogramClippedCells"] = self.histogram_clipped_cells
        if self.extent_source is not None:
            counts["extentSource"] = self.extent_source
        return counts


_EXTENT_SOURCE_RANK = {"proj": 0, "footprint": 1, "none": 2}


def _weakest_extent_source(a: str | None, b: str | None) -> str | None:
    if a is None or b is None:
        return a or b
    return max(a, b, key=lambda source: _EXTENT_SOURCE_RANK.get(source, 3))


def _single_time_slice(data: DataArray) -> DataArray:
    if "time" in data.dims:
        if data.sizes["time"] != 1:
            raise CSDRException(
                f"Zonal statistics need exactly one time slice, but {data.sizes['time']} "
                "were loaded. Narrow --datetime-string-match to one source date."
            )
        data = data.isel(time=0)
    if data.ndim != 2:
        raise CSDRException(
            f"Zonal statistics need a 2-D raster band; got dimensions {data.dims}."
        )
    return data


def xarray_zonal_cells(
    data: Dataset | DataArray,
    geometry_4326: Geometry,
    indicator: str | None = None,
    *,
    all_touched: bool = True,
    extent: Geometry | None = None,
    collect_values: bool = False,
    histogram: PercentileMethod | None = None,
    collect_distinct: bool = False,
) -> ZonalCells:
    """Count, sum and (optionally) collect the valid cells of one band in one pass.

    Uses the same boundary rasterisation and validity rule as
    ``xarray_calculate_area_m2``. ``extent`` (the source raster extent) keeps
    loaded fill cells outside every source raster out of ``boundary_cells``.
    Every requested reduction of a lazy (dask) band is computed together, so
    the source is read once. A part with no valid cells is returned as is; the
    caller decides on support after combining parts.
    """
    if type(data) is not DataArray:
        if indicator is None:
            raise CSDRException("Indicator must be specified when data is a Dataset.")
        data = data[indicator]
    data = _single_time_slice(data)

    geobox = data.odc.geobox
    inside = raster_boundary_mask(geobox, geometry_4326, all_touched)
    if extent is not None:
        in_extent = raster_extent_mask(geobox, extent)
        if in_extent is not None:
            inside = inside & in_extent

    array = data.data
    lazy = dask.is_dask_collection(array)
    xp = dask.array if lazy else np
    inside_array = (
        dask.array.from_array(inside, chunks=array.chunks) if lazy else inside
    )
    valid = raster_valid_mask(array, data.odc.nodata) & inside_array
    # Invalid cells become 0 so no NaN or nodata reaches a sum or a cast.
    values64 = xp.where(valid, array.astype(np.float64), 0.0)

    reductions: dict[str, Any] = {
        "valid_cells": valid.sum(),
        "valid_sum": values64.sum(),
    }
    if histogram is not None:
        lo, hi = histogram.value_range
        bins = histogram.bins
        scaled = (values64 - lo) / (hi - lo) * bins
        index = xp.where(valid, xp.clip(scaled, 0, bins - 1).astype(np.int64), bins)
        # Bin ``bins`` is the sink for invalid cells and is never read.
        reductions["histogram"] = xp.bincount(index.ravel(), minlength=bins + 1)
        reductions["clipped"] = (valid & ((values64 < lo) | (values64 > hi))).sum()
    if collect_values or collect_distinct:
        valid_values = array[valid]
        if collect_values:
            reductions["values"] = valid_values
        if collect_distinct:
            reductions["distinct"] = xp.unique(valid_values)
    if lazy:
        (reductions,) = dask.compute(reductions)

    cells = ZonalCells(
        boundary_cells=int(inside.sum()),
        valid_cells=int(reductions["valid_cells"]),
        valid_sum=float(reductions["valid_sum"]),
    )
    if histogram is not None:
        cells.histogram = np.asarray(reductions["histogram"], dtype=np.int64)
        cells.histogram_clipped_cells = int(reductions["clipped"])
        cells.histogram_method = histogram
    if collect_values:
        cells.values = [np.asarray(reductions["values"])]
    if collect_distinct:
        cells.distinct = np.asarray(reductions["distinct"])
    return cells


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
