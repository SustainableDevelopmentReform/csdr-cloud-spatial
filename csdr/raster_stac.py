"""Generic raster conversion to Cloud Optimised GeoTIFFs indexed by STAC items.

Engine behind ``csdr convert raster-to-stac``. It reads one band of a GeoTIFF
(local path or store URL) and writes, without ever warping (the source CRS,
resolution and pixel grid are kept):

* ``<target>/cogs/<slug>_<row>_<col>.tif``: one COG per tile (DEFLATE,
  512-pixel blocks, overviews), dtype and nodata preserved. Tiles are windows
  aligned to the source pixel grid, starting at its top-left pixel; tiles with
  no valid cell are skipped and counted;
* ``<target>/items/<slug>_<row>_<col>.json``: one STAC item per COG
  (``rio_stac.create_stac_item``) with the ``proj`` and ``raster`` extensions
  and, given a classification table, ``classification:classes`` on the asset;
* ``<target>/collection.json``: the collection the items link to;
* optionally ``<display>/<slug>_display.tif``: one coarser COG of the whole
  raster for map display, in the source CRS or, with ``display_crs``,
  reprojected (the analysis COGs are never warped).

``csdr stac index`` then indexes the items into the ``stac-geoparquet``
dataset contract the product engine reads. The source bytes (and the
classification table's) are hashed with SHA-256 and a declared hash is
verified before anything is written. See ``docs/raster-to-stac.md``.
"""

import json
import logging
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from xml.sax.saxutils import escape

import geopandas as gpd
import numpy as np
import pandas as pd
import pystac
import rasterio
import rasterio.shutil
from affine import Affine
from obstore.store import ObjectStore
from pystac.extensions.classification import Classification, ClassificationExtension
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import array_bounds
from rasterio.warp import calculate_default_transform, reproject
from rasterio.warp import transform as transform_points
from rasterio.windows import Window
from rio_stac import create_stac_item

from csdr.io import (
    exists,
    get_store_with_prefix_from_url,
    read_dict,
    split_path_and_file_name_from_url,
    write_json,
)
from csdr.utils import (
    CSDRException,
    download_with_sha256,
    is_remote_url,
    local_file_path,
    normalise_sha256,
    sha256_file,
    url_suffix,
    verify_sha256,
)

logger = logging.getLogger(__name__)

RASTER_SUFFIXES = (".tif", ".tiff")
TABLE_SUFFIXES = (".dbf", ".csv", ".json", ".xml")
COG_OPTIONS = {"COMPRESS": "DEFLATE", "BLOCKSIZE": 512, "OVERVIEWS": "AUTO"}
DEFAULT_DISPLAY_METRES = 250.0
DISPLAY_FACTOR = 10
METRES_PER_DEGREE = 111_320.0
# Cells read per call while checking a tile for valid data.
SCAN_PIXELS = 16 * 1024 * 1024
# Class object fields defined by the classification extension (v2.0.0).
CLASS_FIELDS = frozenset(
    (
        "value",
        "name",
        "title",
        "description",
        "color_hint",
        "nodata",
        "percentage",
        "count",
    )
)
NAME_COLUMN_CANDIDATES = ("name", "label", "class", "title")
_CLASS_NAME_UNSAFE = re.compile(r"[^0-9A-Za-z_-]+")
_COLOR_HINT = re.compile(r"^#?([0-9A-Fa-f]{6})$")
_BINNED_VALUE = "__binned_value__"
# Report at most this many raster values that have no class.
MAX_REPORTED_VALUES = 20
_GDAL_TYPES = {
    "uint8": "Byte",
    "int8": "Int8",
    "uint16": "UInt16",
    "int16": "Int16",
    "uint32": "UInt32",
    "int32": "Int32",
    "uint64": "UInt64",
    "int64": "Int64",
    "float32": "Float32",
    "float64": "Float64",
}
_RESAMPLING = {r.name: r for r in Resampling}


# ---------------------------------------------------------------------------
# Inputs: staging, datetimes, nodata
# ---------------------------------------------------------------------------


@dataclass
class StagedFile:
    """A local readable copy (or the original path) of a source and its SHA-256."""

    path: Path
    sha256: str


@contextmanager
def stage_file(
    source: str, suffixes: tuple[str, ...], kind: str
) -> Iterator[StagedFile]:
    """Yield a local path for ``source`` and the SHA-256 of its bytes.

    Local files are read in place. Store URLs (``s3://``, ``https://``) are
    streamed to a temporary directory that is removed afterwards.
    """
    name = source.lower()
    if not any(name.endswith(suffix) for suffix in suffixes):
        raise CSDRException(
            f"Unsupported {kind} '{source}'. Supported extensions: {', '.join(suffixes)}."
        )
    if not is_remote_url(source):
        path = local_file_path(source)
        if not path.is_file():
            raise CSDRException(f"{kind.capitalize()} does not exist: {source}")
        yield StagedFile(path, sha256_file(path))
        return
    directory_url, file_name = split_path_and_file_name_from_url(source)
    store = get_store_with_prefix_from_url(directory_url)
    with TemporaryDirectory(prefix="csdr-raster-") as directory:
        target = Path(directory) / file_name
        try:
            sha256 = download_with_sha256(store, file_name, target)
        except FileNotFoundError:
            raise CSDRException(
                f"{kind.capitalize()} does not exist: {source}"
            ) from None
        yield StagedFile(target, sha256)


def parse_datetime(value: str) -> datetime:
    """Parse an ISO 8601 date or datetime; a naive value is taken as UTC.

    A bare year (``2023``) or year-month (``2023-07``) means its first day.
    """
    text = value.strip()
    if re.fullmatch(r"\d{4}", text):
        text += "-01-01"
    elif re.fullmatch(r"\d{4}-\d{2}", text):
        text += "-01"
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise CSDRException(
            f"--datetime '{value}' is not an ISO 8601 date or datetime, e.g. 2023-01-01."
        ) from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_float(dtype: str) -> bool:
    return np.issubdtype(np.dtype(dtype), np.floating)


def resolve_nodata(
    dtype: str, source_nodata: float | None, override: float | None
) -> float | None:
    """Nodata for the outputs: ``override`` when given, else the source's."""
    if override is None:
        if source_nodata is None or _is_float(dtype):
            return source_nodata
        return int(source_nodata)
    if _is_float(dtype):
        return float(override)
    if not float(override).is_integer():
        raise CSDRException(f"--nodata {override} is not an integer for {dtype} data.")
    info = np.iinfo(np.dtype(dtype))
    if not info.min <= override <= info.max:
        raise CSDRException(
            f"--nodata {override} is outside the {dtype} range [{info.min}, {info.max}]."
        )
    return int(override)


def _nodata_text(nodata: float) -> str:
    if isinstance(nodata, int):
        return str(nodata)
    if math.isnan(nodata):
        return "nan"
    if math.isinf(nodata):
        return "inf" if nodata > 0 else "-inf"
    return repr(float(nodata))


def _same_nodata(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return float(a) == float(b)


# ---------------------------------------------------------------------------
# Tiling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    row: int
    col: int
    window: Window


def plan_tiles(
    width: int, height: int, tile_width: int, tile_height: int
) -> list[Tile]:
    """Windows of ``tile_width`` x ``tile_height`` pixels covering the raster once.

    Tiles start at the top-left pixel; the last row and column are clipped to
    the raster, so every pixel belongs to exactly one tile.
    """
    if tile_width < 1 or tile_height < 1:
        raise CSDRException("Tile sizes must be at least one pixel.")
    tiles = []
    for row, row_off in enumerate(range(0, height, tile_height)):
        for col, col_off in enumerate(range(0, width, tile_width)):
            window = Window(
                col_off,
                row_off,
                min(tile_width, width - col_off),
                min(tile_height, height - row_off),
            )
            tiles.append(Tile(row, col, window))
    return tiles


def tile_size_from_degrees(
    crs: CRS, transform: Affine, width: int, height: int, degrees: float
) -> tuple[int, int]:
    """Approximate pixel counts (width, height) spanning ``degrees`` at the centre.

    Exact for a geographic CRS. For a projected CRS, the distance covered by
    ``degrees`` of longitude and of latitude at the raster's centre is measured
    in the source CRS and divided by the pixel size, so tiles stay aligned to
    the source grid and are only approximately ``degrees`` wide away from the
    centre.
    """
    if degrees <= 0:
        raise CSDRException("--tile-degrees must be positive.")
    res_x, res_y = abs(transform.a), abs(transform.e)
    if crs.is_geographic:
        return max(1, round(degrees / res_x)), max(1, round(degrees / res_y))
    cx, cy = transform * (width / 2, height / 2)
    (lon,), (lat,) = transform_points(crs, "EPSG:4326", [cx], [cy])
    half = degrees / 2
    xs, ys = transform_points(
        "EPSG:4326",
        crs,
        [lon - half, lon + half, lon, lon],
        [lat, lat, lat - half, lat + half],
    )
    span_x = math.hypot(xs[1] - xs[0], ys[1] - ys[0])
    span_y = math.hypot(xs[3] - xs[2], ys[3] - ys[2])
    return max(1, round(span_x / res_x)), max(1, round(span_y / res_y))


def _index_label(index: int, count: int) -> str:
    return str(index).zfill(len(str(max(count - 1, 0))))


# ---------------------------------------------------------------------------
# Windowed VRTs and COG writing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceGrid:
    """The band being converted: where it is and the grid it sits on."""

    path: Path
    band: int
    dtype: str
    crs: CRS
    transform: Affine
    width: int
    height: int
    nodata: float | None

    @property
    def resolution(self) -> tuple[float, float]:
        return abs(self.transform.a), abs(self.transform.e)


def read_source_grid(path: Path, band: int) -> SourceGrid:
    """Open ``path`` and check that ``band`` can be converted without warping."""
    with rasterio.open(path) as dataset:
        if not 1 <= band <= dataset.count:
            raise CSDRException(
                f"--band {band} is not in the source (bands 1-{dataset.count})."
            )
        if dataset.crs is None:
            raise CSDRException("The source has no CRS; it cannot be indexed.")
        if dataset.transform.b != 0 or dataset.transform.d != 0:
            raise CSDRException(
                "The source grid is rotated; only north-up rasters are supported."
            )
        dtype = dataset.dtypes[band - 1]
        if dtype not in _GDAL_TYPES:
            raise CSDRException(f"Unsupported raster data type {dtype}.")
        return SourceGrid(
            path=path,
            band=band,
            dtype=dtype,
            crs=dataset.crs,
            transform=dataset.transform,
            width=dataset.width,
            height=dataset.height,
            nodata=dataset.nodatavals[band - 1],
        )


def window_vrt(grid: SourceGrid, window: Window, nodata: float | None) -> str:
    """A VRT document exposing one window of one band, with ``nodata`` declared.

    GDAL copies from it block by block, so a tile never has to fit in memory,
    and the declared nodata reaches the COG even when the source has none.
    """
    transform = grid.transform
    x, y = int(window.col_off), int(window.row_off)
    w, h = int(window.width), int(window.height)
    origin_x, origin_y = transform * (x, y)
    geotransform = ", ".join(
        repr(float(v))
        for v in (
            origin_x,
            transform.a,
            transform.b,
            origin_y,
            transform.d,
            transform.e,
        )
    )
    nodata_xml = (
        f"<NoDataValue>{_nodata_text(nodata)}</NoDataValue>"
        if nodata is not None
        else ""
    )
    return (
        f'<VRTDataset rasterXSize="{w}" rasterYSize="{h}">'
        f"<SRS>{escape(grid.crs.to_wkt())}</SRS>"
        f"<GeoTransform>{geotransform}</GeoTransform>"
        f'<VRTRasterBand dataType="{_GDAL_TYPES[grid.dtype]}" band="1">{nodata_xml}'
        "<SimpleSource>"
        f'<SourceFilename relativeToVRT="0">{escape(str(grid.path.resolve()))}</SourceFilename>'
        f"<SourceBand>{grid.band}</SourceBand>"
        f'<SrcRect xOff="{x}" yOff="{y}" xSize="{w}" ySize="{h}"/>'
        f'<DstRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>'
        "</SimpleSource></VRTRasterBand></VRTDataset>"
    )


def window_has_valid_cells(
    dataset: rasterio.io.DatasetReader, window: Window, nodata: float | None
) -> bool:
    """True when any cell in ``window`` is finite and not ``nodata``.

    Reads strips of the window and stops at the first valid cell, so a tile
    with data is usually decided after one read.
    """
    w, h = int(window.width), int(window.height)
    rows = max(1, SCAN_PIXELS // max(w, 1))
    nan_nodata = nodata is not None and math.isnan(nodata)
    for offset in range(0, h, rows):
        strip = Window(
            window.col_off, window.row_off + offset, w, min(rows, h - offset)
        )
        data = dataset.read(1, window=strip)
        valid = (
            np.isfinite(data) if data.dtype.kind == "f" else np.ones(data.shape, bool)
        )
        if nodata is not None and not nan_nodata:
            valid &= data != nodata
        if valid.any():
            return True
    return False


def default_overview_resampling(dtype: str) -> str:
    """Mode keeps class codes for integer data; average suits continuous data."""
    return "average" if _is_float(dtype) else "mode"


def _resampling(name: str) -> Resampling:
    key = name.strip().lower()
    if key not in _RESAMPLING:
        raise CSDRException(
            f"Unknown resampling '{name}'. Use one of: {', '.join(sorted(_RESAMPLING))}."
        )
    return _RESAMPLING[key]


def write_cog(
    source: str | Path,
    target: Path,
    dtype: str,
    overview_resampling: str,
    num_threads: str,
) -> None:
    """Copy ``source`` (a raster or VRT) to a COG with the toolkit's options."""
    options = dict(COG_OPTIONS)
    options["OVERVIEW_RESAMPLING"] = _resampling(overview_resampling).name.upper()
    options["NUM_THREADS"] = num_threads
    options["BIGTIFF"] = "IF_SAFER"
    if _is_float(dtype):
        options["PREDICTOR"] = "YES"
    target.parent.mkdir(parents=True, exist_ok=True)
    rasterio.shutil.copy(str(source), str(target), driver="COG", **options)


# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------


def _read_gdal_rat(path: Path, band: int) -> pd.DataFrame:
    """Read a GDAL PAM raster attribute table (``.aux.xml``) for ``band``."""
    root = ET.parse(path).getroot()
    rat = None
    for pam_band in root.iter("PAMRasterBand"):
        if pam_band.get("band", "1") == str(band):
            rat = pam_band.find("GDALRasterAttributeTable")
            break
    if rat is None:
        tables = list(root.iter("GDALRasterAttributeTable"))
        if len(tables) != 1:
            raise CSDRException(
                f"No raster attribute table for band {band} in {path.name}."
            )
        rat = tables[0]
    fields = sorted(rat.findall("FieldDefn"), key=lambda f: int(f.get("index", 0)))
    names = [f.findtext("Name") or f"field_{i}" for i, f in enumerate(fields)]
    types = [int(f.findtext("Type") or 2) for f in fields]  # 0 int, 1 real, 2 string
    rows = sorted(rat.findall("Row"), key=lambda r: int(r.get("index", 0)))
    records = []
    for row in rows:
        cells = [cell.text for cell in row.findall("F")]
        record = {}
        for name, kind, text in zip(names, types, cells, strict=False):
            if text is None or text == "":
                record[name] = None
            elif kind == 0:
                record[name] = int(float(text))
            elif kind == 1:
                record[name] = float(text)
            else:
                record[name] = text
        records.append(record)
    table = pd.DataFrame.from_records(records, columns=names)
    # Linear-binned tables carry no value column: value = Row0Min + index * BinSize.
    if rat.get("Row0Min") is not None and rat.get("BinSize") is not None:
        start, size = float(rat.get("Row0Min")), float(rat.get("BinSize"))
        values = [start + int(r.get("index", i)) * size for i, r in enumerate(rows)]
        table.insert(0, _BINNED_VALUE, values)
    return table


def read_table(path: Path, band: int = 1) -> pd.DataFrame:
    """Read a classification table: ``.dbf``, ``.csv``, ``.json`` or GDAL ``.aux.xml``."""
    suffix = url_suffix(path.name)
    if suffix == ".dbf":
        return pd.DataFrame(gpd.read_file(path, ignore_geometry=True))
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("classes"), list):
            data = data["classes"]
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise CSDRException(
                "A JSON classification table must be a list of objects such as "
                '[{"value": 1, "name": "Forest"}].'
            )
        return pd.DataFrame.from_records(data)
    if suffix == ".xml":
        return _read_gdal_rat(path, band)
    raise CSDRException(
        f"Unsupported classification table '{path.name}'. Supported: "
        f"{', '.join(TABLE_SUFFIXES)} (GDAL .aux.xml raster attribute tables)."
    )


def _resolve_column(table: pd.DataFrame, requested: str, option: str) -> str:
    if requested in table.columns:
        return requested
    matches = [c for c in table.columns if str(c).lower() == requested.lower()]
    if len(matches) == 1:
        return matches[0]
    raise CSDRException(
        f"{option} '{requested}' is not a column of the classification table. "
        f"Columns: {', '.join(map(str, table.columns))}."
    )


def _default_name_column(table: pd.DataFrame) -> str:
    lower = {str(c).lower(): c for c in table.columns}
    for candidate in NAME_COLUMN_CANDIDATES:
        if candidate in lower:
            return lower[candidate]
    for column in table.columns:
        if str(column).lower().endswith("name"):
            return column
    raise CSDRException(
        "No class name column found (tried name, label, class, title, then any column "
        "ending in 'name'). Pass --class-name-column. Columns: "
        f"{', '.join(map(str, table.columns))}."
    )


def _json_value(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def class_name(label: str | None, value: int) -> str:
    """Machine-readable class name: letters, digits, '-' and '_' only."""
    slug = _CLASS_NAME_UNSAFE.sub("-", label or "").strip("-")
    return slug or f"class-{value}"


def classification_classes(
    table: pd.DataFrame,
    value_column: str = "Value",
    name_column: str | None = None,
    extra_columns: list[str] | None = None,
    nodata: float | None = None,
) -> list[dict]:
    """STAC ``classification:classes`` entries from a table.

    Each class has the integer ``value``, a machine-safe ``name`` derived from
    the name column, the original text as ``title``, ``nodata: true`` when the
    value is the raster's nodata, ``color_hint`` from a ``color_hint`` column
    (six hex digits, optional ``#``), and each extra column under its own name
    (missing values omitted).
    """
    if table.empty:
        raise CSDRException("The classification table has no rows.")
    named = [c for c in table.columns if c != _BINNED_VALUE]
    if _BINNED_VALUE in table.columns and not any(
        str(c).lower() == value_column.lower() for c in named
    ):
        # A linear-binned GDAL table has no value column; read_table derived it.
        value_col = _BINNED_VALUE
    else:
        value_col = _resolve_column(table[named], value_column, "--class-value-column")
    name_col = (
        _resolve_column(table, name_column, "--class-name-column")
        if name_column
        else _default_name_column(table)
    )
    extras = []
    for column in extra_columns or []:
        resolved = _resolve_column(table, column, "--class-extra-columns")
        if resolved in CLASS_FIELDS:
            raise CSDRException(
                f"Extra column '{resolved}' collides with a classification class field."
            )
        extras.append(resolved)
    lower = {str(c).lower(): c for c in table.columns}
    color_col = lower.get("color_hint")
    classes = []
    seen = set()
    for _, row in table.iterrows():
        raw = _json_value(row[value_col])
        if raw is None:
            raise CSDRException(f"A classification table row has no {value_col}.")
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise CSDRException(f"Class value '{raw}' is not a number.") from None
        if not number.is_integer():
            raise CSDRException(f"Class value {raw} is not an integer.")
        value = int(number)
        if value in seen:
            raise CSDRException(f"Class value {value} appears more than once.")
        seen.add(value)
        label = _json_value(row[name_col])
        entry: dict[str, object] = {
            "value": value,
            "name": class_name(None if label is None else str(label), value),
        }
        if label is not None:
            entry["title"] = str(label)
        if nodata is not None and not math.isnan(nodata) and value == nodata:
            entry["nodata"] = True
        color = _json_value(row[color_col]) if color_col is not None else None
        if color is not None:
            match = _COLOR_HINT.match(str(color))
            if match is None:
                raise CSDRException(
                    f"color_hint '{color}' of class {value} is not six hex digits."
                )
            entry["color_hint"] = match.group(1).upper()
        for column in extras:
            extra = _json_value(row[column])
            if extra is not None:
                entry[str(column)] = extra
        classes.append(entry)
    return sorted(classes, key=lambda c: c["value"])


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


@dataclass
class RasterConversionResult:
    """What a conversion read and wrote; the command records it in its step."""

    source: str
    source_sha256: str
    target_location: str
    collection_id: str
    collection_url: str
    crs: str
    resolution: tuple[float, float]
    dtype: str
    width: int
    height: int
    source_nodata: float | None
    nodata: float | None
    tile_width: int
    tile_height: int
    tiles_planned: int
    tile_degrees: float | None = None
    classification_table: str | None = None
    classification_table_sha256: str | None = None
    class_count: int | None = None
    cog_urls: list[str] = field(default_factory=list)
    item_urls: list[str] = field(default_factory=list)
    skipped_all_nodata: int = 0
    reused_items: int = 0
    stale_items_removed: int = 0
    value_checked_tiles: int = 0
    stale_items_kept: int = 0
    raster_values_without_class: dict | None = None
    display_copy_url: str | None = None
    display_resolution: list[float] | None = None
    display_resolution_requested: float | None = None
    display_crs: str | None = None


def default_slug(source: str) -> str:
    name = split_path_and_file_name_from_url(source)[1]
    stem = name
    for suffix in RASTER_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def _absolute_location(location: str) -> str:
    location = location.rstrip("/")
    if is_remote_url(location):
        return location
    return str(local_file_path(location).absolute())


def _crs_label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else crs.to_string()


def _local_output(location: str, name: str, scratch: Path) -> Path:
    """Where to write an output file before it is stored.

    Local targets are written in place; store targets go to ``scratch`` first.
    """
    if is_remote_url(location):
        return scratch / PurePosixPath(name).name
    path = Path(location) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _store_output(store: ObjectStore, location: str, name: str, path: Path) -> str:
    """Upload ``path`` to a store target (no-op for a local target); return its URL."""
    if is_remote_url(location):
        store.put(name, path)
        path.unlink()
    return f"{location}/{name}"


def _display_resolution(
    crs: CRS, res: tuple[float, float], requested: float | None
) -> float:
    finest = max(res)
    if requested is not None:
        if requested <= finest:
            raise CSDRException(
                f"--display-resolution {requested} must be coarser than the source "
                f"resolution {finest}."
            )
        return float(requested)
    if crs.is_geographic:
        floor = DEFAULT_DISPLAY_METRES / METRES_PER_DEGREE
    else:
        floor = DEFAULT_DISPLAY_METRES / (crs.linear_units_factor[1] or 1.0)
    return max(floor, DISPLAY_FACTOR * finest)


def parse_display_crs(value: str | None) -> CRS | None:
    """``--display-crs``: an EPSG code (``EPSG:3857`` or ``3857``) or other CRS string."""
    if value is None:
        return None
    text = value.strip()
    try:
        return CRS.from_epsg(int(text)) if text.isdigit() else CRS.from_user_input(text)
    except Exception as error:  # noqa: BLE001 - rasterio raises CRSError and others; all mean "not a CRS"
        raise CSDRException(f"--display-crs '{value}' is not a CRS: {error}") from None


@dataclass(frozen=True)
class DisplayPlan:
    """How the display copy is made.

    ``read_resolution`` (source CRS units) is the decimated read of the source;
    with a display CRS that array is then reprojected at ``resolution``
    (display CRS units). Without one, both are the same.
    """

    crs: CRS
    resolution: float
    read_resolution: float


def plan_display(
    grid: SourceGrid, display_crs: CRS | None, requested: float | None
) -> DisplayPlan:
    """Display resolution in the display CRS; ``requested`` is in its units."""
    if display_crs is None or display_crs == grid.crs:
        resolution = _display_resolution(grid.crs, grid.resolution, requested)
        return DisplayPlan(grid.crs, resolution, resolution)
    bounds = array_bounds(grid.height, grid.width, grid.transform)
    native, _, _ = calculate_default_transform(
        grid.crs, display_crs, grid.width, grid.height, *bounds
    )
    native_res = max(abs(native.a), abs(native.e))
    resolution = _display_resolution(display_crs, (native_res, native_res), requested)
    # Read the source at the equivalent resolution, then reproject that array.
    read_resolution = resolution * max(grid.resolution) / native_res
    return DisplayPlan(display_crs, resolution, read_resolution)


def write_display_copy(
    dataset: rasterio.io.DatasetReader,
    target: Path,
    plan: DisplayPlan,
    resampling: str,
    nodata: float | None,
    num_threads: str,
) -> list[float]:
    """Write the whole raster as one coarse COG, reprojected when the plan says so.

    Returns the written pixel size (x, y): width and height are whole pixels
    over the exact source bounds, so it can differ slightly from the plan.

    The source is read once at ``plan.read_resolution`` (GDAL resampling, using
    source overviews when present); a different display CRS is then reached by
    reprojecting that small array, so the full-resolution data is never warped.
    """
    method = _resampling(resampling)
    res_x, res_y = abs(dataset.transform.a), abs(dataset.transform.e)
    width = max(1, math.ceil(dataset.width * res_x / plan.read_resolution))
    height = max(1, math.ceil(dataset.height * res_y / plan.read_resolution))
    data = dataset.read(1, out_shape=(height, width), resampling=method)
    transform = dataset.transform * Affine.scale(
        dataset.width / width, dataset.height / height
    )
    crs = dataset.crs
    if plan.crs != dataset.crs:
        dst_transform, dst_width, dst_height = calculate_default_transform(
            dataset.crs,
            plan.crs,
            width,
            height,
            *array_bounds(height, width, transform),
            resolution=plan.resolution,
        )
        fill = nodata if nodata is not None else 0
        warped = np.full((dst_height, dst_width), fill, dtype=data.dtype)
        reproject(
            data,
            warped,
            src_transform=transform,
            src_crs=dataset.crs,
            src_nodata=nodata,
            dst_transform=dst_transform,
            dst_crs=plan.crs,
            dst_nodata=nodata,
            resampling=method,
        )
        data, transform, crs = warped, dst_transform, plan.crs
        height, width = data.shape
    profile = {
        "driver": "COG",
        "width": width,
        "height": height,
        "count": 1,
        "dtype": data.dtype.name,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "NUM_THREADS": num_threads,
        "OVERVIEW_RESAMPLING": method.name.upper(),
        **COG_OPTIONS,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(target, "w", **profile) as output:
        output.write(data, 1)
    return [abs(transform.a), abs(transform.e)]


@dataclass(frozen=True)
class _ItemTemplate:
    """What every item of one conversion shares."""

    asset_name: str
    item_datetime: datetime
    collection_id: str
    collection_url: str
    properties: dict | None
    classes: list[dict] | None


def _stac_item(
    cog_path: Path, cog_url: str, item_id: str, item_url: str, template: _ItemTemplate
) -> dict:
    asset_name = template.asset_name
    item = create_stac_item(
        str(cog_path),
        input_datetime=template.item_datetime,
        collection=template.collection_id,
        collection_url=template.collection_url,
        properties=dict(template.properties) if template.properties else None,
        id=item_id,
        asset_name=asset_name,
        asset_roles=["data"],
        asset_media_type=pystac.MediaType.COG,
        asset_href=cog_url,
        with_proj=True,
        with_raster=True,
    )
    if template.classes:
        extension = ClassificationExtension.ext(
            item.assets[asset_name], add_if_missing=True
        )
        extension.classes = [Classification(dict(c)) for c in template.classes]
    document = item.to_dict(include_self_link=False, transform_hrefs=False)
    document["links"].append(
        {"rel": "self", "href": item_url, "type": pystac.MediaType.JSON}
    )
    return document


def _union_bbox(boxes: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _collection_document(
    collection_id: str,
    description: str,
    bboxes: list[list[float]],
    item_datetime: datetime,
    asset_name: str,
    dtype: str,
    nodata: float | None,
    classes: list[dict] | None,
    collection_url: str,
    existing: dict | None,
) -> dict:
    start = end = item_datetime
    if existing is not None:
        extent = existing.get("extent", {})
        bboxes = bboxes + extent.get("spatial", {}).get("bbox", [])
        for interval in extent.get("temporal", {}).get("interval", []):
            for value in interval:
                if value:
                    moment = parse_datetime(value)
                    start, end = min(start, moment), max(end, moment)
    collection = pystac.Collection(
        id=collection_id,
        description=description,
        extent=pystac.Extent(
            pystac.SpatialExtent(
                [_union_bbox(bboxes)] if bboxes else [[-180, -90, 180, 90]]
            ),
            pystac.TemporalExtent([[start, end]]),
        ),
        license="other",
    )
    document = collection.to_dict(include_self_link=False, transform_hrefs=False)
    band: dict[str, object] = {"data_type": dtype}
    if nodata is not None:
        band["nodata"] = "nan" if math.isnan(nodata) else nodata
    asset: dict[str, object] = {
        "type": pystac.MediaType.COG,
        "roles": ["data"],
        "raster:bands": [band],
    }
    extensions = ["https://stac-extensions.github.io/raster/v1.1.0/schema.json"]
    if classes:
        asset["classification:classes"] = classes
        extensions.append(
            "https://stac-extensions.github.io/classification/v2.0.0/schema.json"
        )
    document["stac_extensions"] = extensions
    document["item_assets"] = {asset_name: asset}
    document["links"].append(
        {"rel": "self", "href": collection_url, "type": pystac.MediaType.JSON}
    )
    return document


def convert_raster(
    source: str,
    target_location: str,
    *,
    asset_name: str,
    item_datetime: datetime,
    collection_id: str | None = None,
    target_filename: str | None = None,
    band: int = 1,
    tile_size: int | None = None,
    tile_degrees: float | None = None,
    classification_table: str | None = None,
    class_value_column: str = "Value",
    class_name_column: str | None = None,
    class_extra_columns: list[str] | None = None,
    display_copy: str | None = None,
    display_resolution: float | None = None,
    display_resampling: str | None = None,
    display_crs: str | None = None,
    nodata: float | None = None,
    source_sha256: str | None = None,
    item_properties: dict | None = None,
    overview_resampling: str | None = None,
    num_threads: str = "ALL_CPUS",
    overwrite: bool = True,
) -> RasterConversionResult:
    """Convert one raster band; see the module docstring for the outputs."""
    declared_sha256 = normalise_sha256(source_sha256)
    if tile_size is not None and tile_degrees is not None:
        raise CSDRException("Give --tile-size or --tile-degrees, not both.")
    if tile_size is not None and tile_size < 1:
        raise CSDRException("--tile-size must be at least one pixel.")
    if item_properties and "datetime" in item_properties:
        raise CSDRException(
            "Set the item datetime with --datetime, not --item-properties."
        )
    if not asset_name.strip():
        raise CSDRException("--asset-name must not be empty.")
    slug = target_filename or default_slug(source)
    collection_id = collection_id or slug
    target_location = _absolute_location(target_location)
    display_location = _absolute_location(display_copy) if display_copy else None
    display_target_crs = parse_display_crs(display_crs)
    if display_target_crs is not None and display_location is None:
        raise CSDRException("--display-crs needs --display-copy.")
    collection_url = f"{target_location}/collection.json"

    store = get_store_with_prefix_from_url(target_location)
    existing = (
        read_dict(store, "collection.json")
        if exists(store, "collection.json")
        else None
    )
    if existing is not None and existing.get("id") != collection_id:
        raise CSDRException(
            f"The target already holds collection '{existing.get('id')}'; "
            f"--collection '{collection_id}' would replace it. Use another target "
            "or the same collection id."
        )

    with stage_file(source, RASTER_SUFFIXES, "raster source") as staged:
        verify_sha256(declared_sha256, staged.sha256)
        grid = read_source_grid(staged.path, band)
        out_nodata = resolve_nodata(grid.dtype, grid.nodata, nodata)
        classes, table_sha256 = None, None
        if classification_table is not None:
            with stage_file(
                classification_table, TABLE_SUFFIXES, "classification table"
            ) as table_file:
                table_sha256 = table_file.sha256
                classes = classification_classes(
                    read_table(table_file.path, band),
                    value_column=class_value_column,
                    name_column=class_name_column,
                    extra_columns=class_extra_columns,
                    nodata=out_nodata,
                )
        if tile_degrees is not None:
            tile_w, tile_h = tile_size_from_degrees(
                grid.crs, grid.transform, grid.width, grid.height, tile_degrees
            )
        else:
            tile_w = tile_size or grid.width
            tile_h = tile_size or grid.height
        tiles = plan_tiles(grid.width, grid.height, tile_w, tile_h)

        result = RasterConversionResult(
            source=source,
            source_sha256=staged.sha256,
            target_location=target_location,
            collection_id=collection_id,
            collection_url=collection_url,
            crs=_crs_label(grid.crs),
            resolution=grid.resolution,
            dtype=grid.dtype,
            width=grid.width,
            height=grid.height,
            source_nodata=grid.nodata,
            nodata=out_nodata,
            tile_width=tile_w,
            tile_height=tile_h,
            tiles_planned=len(tiles),
            tile_degrees=tile_degrees,
            classification_table=classification_table,
            classification_table_sha256=table_sha256,
            class_count=len(classes) if classes is not None else None,
        )
        item = _ItemTemplate(
            asset_name=asset_name,
            item_datetime=item_datetime,
            collection_id=collection_id,
            collection_url=collection_url,
            properties=item_properties,
            classes=classes,
        )
        with TemporaryDirectory(prefix="csdr-raster-") as directory:
            scratch = Path(directory)
            full_vrt = scratch / "source.vrt"
            full_vrt.write_text(
                window_vrt(grid, Window(0, 0, grid.width, grid.height), out_nodata),
                encoding="utf-8",
            )
            with rasterio.open(full_vrt) as view:
                check_values = classes is not None and not _is_float(grid.dtype)
                seen_values: set[int] = set()
                bboxes = _write_tiles(
                    view,
                    grid,
                    tiles,
                    slug,
                    item,
                    store,
                    scratch,
                    result,
                    overview_resampling or default_overview_resampling(grid.dtype),
                    num_threads,
                    overwrite,
                    seen_values if check_values else None,
                )
                if not result.item_urls:
                    raise CSDRException(
                        "Every tile is nodata; no COG or item was written. "
                        "Check --nodata and --band."
                    )
                _handle_stale_items(store, slug, collection_id, result, overwrite)
                if check_values:
                    result.raster_values_without_class = _values_without_class(
                        seen_values, classes, out_nodata, result
                    )
                if display_location is not None:
                    plan = plan_display(grid, display_target_crs, display_resolution)
                    display_name = f"{slug}_display.tif"
                    local_display = _local_output(
                        display_location, display_name, scratch
                    )
                    written = write_display_copy(
                        view,
                        local_display,
                        plan,
                        display_resampling or default_overview_resampling(grid.dtype),
                        out_nodata,
                        num_threads,
                    )
                    result.display_copy_url = _store_output(
                        get_store_with_prefix_from_url(display_location),
                        display_location,
                        display_name,
                        local_display,
                    )
                    result.display_resolution = written
                    result.display_resolution_requested = plan.resolution
                    result.display_crs = _crs_label(plan.crs)

    source_name = split_path_and_file_name_from_url(source)[1]
    write_json(
        store,
        "collection.json",
        _collection_document(
            collection_id,
            f"Cloud Optimised GeoTIFFs converted from {source_name} by csdr "
            "convert raster-to-stac.",
            bboxes,
            item_datetime,
            asset_name,
            grid.dtype,
            out_nodata,
            classes,
            collection_url,
            existing,
        ),
    )
    return result


def _write_tiles(
    view: rasterio.io.DatasetReader,
    grid: SourceGrid,
    tiles: list[Tile],
    slug: str,
    item: _ItemTemplate,
    store: ObjectStore,
    scratch: Path,
    result: RasterConversionResult,
    overview_resampling: str,
    num_threads: str,
    overwrite: bool,
    seen_values: set[int] | None = None,
) -> list[list[float]]:
    """Write one COG and STAC item per tile with valid cells; return their bboxes.

    With ``seen_values``, the distinct valid values of every COG written in
    this run are added to it (integer data only).
    """
    location = result.target_location
    rows = max(t.row for t in tiles) + 1
    cols = max(t.col for t in tiles) + 1
    bboxes = []
    for tile in tiles:
        tile_id = (
            f"{slug}_{_index_label(tile.row, rows)}_{_index_label(tile.col, cols)}"
        )
        item_name, cog_name = f"items/{tile_id}.json", f"cogs/{tile_id}.tif"
        item_url, cog_url = f"{location}/{item_name}", f"{location}/{cog_name}"
        if not overwrite and exists(store, item_name) and exists(store, cog_name):
            existing = read_dict(store, item_name)
            shape = existing.get("properties", {}).get("proj:shape")
            window_shape = [int(tile.window.height), int(tile.window.width)]
            if shape is not None and list(shape) != window_shape:
                raise CSDRException(
                    f"Existing item {item_name} is {shape[0]} x {shape[1]} pixels but "
                    f"this tiling makes it {window_shape[0]} x {window_shape[1]}: it "
                    "belongs to another tiling. Use --overwrite or a fresh target."
                )
            bboxes.append(existing["bbox"])
            result.item_urls.append(item_url)
            result.cog_urls.append(cog_url)
            result.reused_items += 1
            continue
        if not window_has_valid_cells(view, tile.window, result.nodata):
            logger.info(f"Tile {tile_id} has no valid cells; skipped.")
            result.skipped_all_nodata += 1
            continue
        tile_vrt = scratch / f"{tile_id}.vrt"
        tile_vrt.write_text(
            window_vrt(grid, tile.window, result.nodata), encoding="utf-8"
        )
        local_cog = _local_output(location, cog_name, scratch)
        write_cog(tile_vrt, local_cog, grid.dtype, overview_resampling, num_threads)
        if seen_values is not None:
            seen_values |= distinct_values(local_cog, result.nodata)
            result.value_checked_tiles += 1
        document = _stac_item(local_cog, cog_url, tile_id, item_url, item)
        _store_output(store, location, cog_name, local_cog)
        write_json(store, item_name, document)
        bboxes.append(document["bbox"])
        result.item_urls.append(item_url)
        result.cog_urls.append(cog_url)
        logger.info(f"Wrote {cog_url} and {item_url}.")
    return bboxes


def distinct_values(path: Path, nodata: float | None) -> set[int]:
    """Distinct non-nodata values of an integer raster, read in row strips."""
    found: set[int] = set()
    with rasterio.open(path) as dataset:
        small = dataset.dtypes[0] in ("uint8", "uint16")
        present = np.zeros(65536, bool) if small else None
        rows = max(1, SCAN_PIXELS // max(dataset.width, 1))
        for offset in range(0, dataset.height, rows):
            strip = dataset.read(
                1,
                window=Window(
                    0, offset, dataset.width, min(rows, dataset.height - offset)
                ),
            )
            if present is not None:
                present[: 1 + int(strip.max())] |= (
                    np.bincount(strip.ravel(), minlength=1) > 0
                )
            else:
                found.update(int(v) for v in np.unique(strip))
    if present is not None:
        found = {int(v) for v in np.flatnonzero(present)}
    if nodata is not None and not math.isnan(nodata):
        found.discard(int(nodata))
    return found


def _values_without_class(
    seen: set[int],
    classes: list[dict],
    nodata: float | None,
    result: RasterConversionResult,
) -> dict:
    """Raster values found in the written COGs that the table does not list."""
    missing = sorted(seen - {c["value"] for c in classes})
    if missing:
        shown = ", ".join(map(str, missing[:MAX_REPORTED_VALUES]))
        logger.warning(
            f"{len(missing)} raster values have no class in the classification "
            f"table (first {min(len(missing), MAX_REPORTED_VALUES)}: {shown})."
        )
    return {
        "count": len(missing),
        "values": missing[:MAX_REPORTED_VALUES],
        "tiles_checked": result.value_checked_tiles,
    }


def _handle_stale_items(
    store: ObjectStore,
    slug: str,
    collection_id: str,
    result: RasterConversionResult,
    overwrite: bool,
) -> None:
    """Deal with items of this slug and collection that this run did not write.

    They come from an earlier run with another tiling (or a tile that is now
    all nodata). With overwrite they are removed with their COGs, so the
    folder holds exactly this run's items; with --no-overwrite they are kept
    and counted with a warning. Item ids always end in two tile indices, so
    the pattern never matches another slug's items.
    """
    pattern = re.compile(rf"{re.escape(slug)}_\d+_\d+\.json")
    current = {url.rsplit("/", 1)[-1] for url in result.item_urls}
    stale = []
    for batch in store.list(prefix="items/", chunk_size=1000):
        for entry in batch:
            name = entry["path"].rsplit("/", 1)[-1]
            if pattern.fullmatch(name) and name not in current:
                stale.append(name)
    for name in sorted(stale):
        try:
            document = read_dict(store, f"items/{name}")
        except (OSError, ValueError):
            continue
        if document.get("collection") != collection_id:
            continue
        if not overwrite:
            result.stale_items_kept += 1
            continue
        store.delete(f"items/{name}")
        cog_name = f"cogs/{name[: -len('.json')]}.tif"
        if exists(store, cog_name):
            store.delete(cog_name)
        result.stale_items_removed += 1
    if result.stale_items_removed:
        logger.info(
            f"Removed {result.stale_items_removed} items (and COGs) of an earlier "
            "tiling that this run did not write."
        )
    if result.stale_items_kept:
        logger.warning(
            f"{result.stale_items_kept} items of an earlier tiling remain in "
            f"{result.target_location}/items/ (--no-overwrite). Indexing the folder "
            "would mix tilings; rerun with --overwrite or use a fresh target."
        )


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def _nodata_json(value: float | None) -> object:
    if value is None:
        return None
    return "nan" if math.isnan(value) else value


def step_inputs(result: RasterConversionResult, **options: object) -> dict[str, object]:
    """Step ``inputs``: source identity, the grid read and the options used."""
    inputs: dict[str, object] = {
        "source": result.source,
        "source_sha256": result.source_sha256,
        "source_crs": result.crs,
        "source_resolution": list(result.resolution),
        "source_shape": [result.height, result.width],
        "data_type": result.dtype,
        "source_nodata": _nodata_json(result.source_nodata),
        "nodata": _nodata_json(result.nodata),
        "tile_size_pixels": [result.tile_width, result.tile_height],
    }
    if result.tile_degrees is not None:
        inputs["tile_degrees"] = result.tile_degrees
    if result.classification_table is not None:
        inputs["classification_table"] = result.classification_table
        inputs["classification_table_sha256"] = result.classification_table_sha256
    inputs.update({k: v for k, v in options.items() if v is not None})
    return inputs


def step_outputs(result: RasterConversionResult) -> dict[str, object]:
    outputs: dict[str, object] = {
        "cogs_count": len(result.cog_urls),
        "items_count": len(result.item_urls),
        "items_prefix": f"{result.target_location}/items/",
        "cogs_prefix": f"{result.target_location}/cogs/",
        "collection_url": result.collection_url,
        "tiles_planned": result.tiles_planned,
        "skipped_all_nodata_tiles": result.skipped_all_nodata,
    }
    if result.reused_items:
        outputs["reused_items"] = result.reused_items
    outputs["stale_items_removed"] = result.stale_items_removed
    if result.stale_items_kept:
        outputs["stale_items_kept"] = result.stale_items_kept
    if result.class_count is not None:
        outputs["class_count"] = result.class_count
    if result.raster_values_without_class is not None:
        outputs["raster_values_without_class"] = result.raster_values_without_class
    if result.display_copy_url is not None:
        outputs["display_copy_url"] = result.display_copy_url
        outputs["display_resolution"] = result.display_resolution
        outputs["display_resolution_requested"] = result.display_resolution_requested
        outputs["display_crs"] = result.display_crs
    return outputs


def method_notes(result: RasterConversionResult) -> list[str]:
    """Human-readable method notes recorded with the step."""
    grid = f"{result.resolution[0]:g} x {result.resolution[1]:g}"
    notes = [
        (
            f"No warping: COGs keep the source grid ({result.crs}, {grid}) and "
            f"data type {result.dtype}."
        ),
        (
            f"Tiles are {result.tile_width} x {result.tile_height}-pixel windows "
            "aligned to the source grid from its top-left pixel; tiles with no "
            "finite, non-nodata cell are not written."
        ),
    ]
    if result.tile_degrees is not None:
        notes.append(
            f"--tile-degrees {result.tile_degrees:g} resolved to "
            f"{result.tile_width} x {result.tile_height} pixels at the raster centre "
            "(approximate away from the centre for a projected CRS)."
        )
    if not _same_nodata(result.source_nodata, result.nodata):
        if result.source_nodata is None:
            notes.append(
                f"The source declares no nodata; --nodata {result.nodata} set."
            )
        else:
            notes.append(
                f"Source nodata {result.source_nodata} replaced by --nodata "
                f"{result.nodata}: cells equal to {result.source_nodata} are now data."
            )
    if result.display_copy_url is not None:
        if result.display_crs == result.crs:
            where = "source CRS units"
        else:
            where = f"{result.display_crs} units (reprojected; the COGs are not)"
        notes.append(
            f"Display copy resampled to {result.display_resolution[0]:g} x "
            f"{result.display_resolution[1]:g} {where} "
            "for map display only; never use it for measurement."
        )
    return notes
