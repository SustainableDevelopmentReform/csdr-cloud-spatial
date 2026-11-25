"""Generic vector conversion to the toolkit's GeoParquet contracts.

One engine behind ``csdr convert vector-to-geoparquet`` and
``csdr convert zip-to-parquet``. It reads a shapefile (bare or zipped),
GeoPackage, GeoJSON or GeoParquet from a local path or a store URL, optionally
filters, selects columns, repairs invalid geometries, adds ``csdr-id`` and
``csdr-name`` (geometry sets) or a constant ``layer`` column (multi-layer
datasets), then writes:

* ``<target>/<slug>.parquet`` in the source's native CRS;
* optionally ``<target>/partition/<slug>_<lon>_<lat>.parquet`` (EPSG:4326, the
  ``geoparquet`` dataset layout the product engine reads);
* optionally ``<target>/<slug>.pmtiles`` with tippecanoe, from an EPSG:4326
  copy.

The source bytes are hashed (SHA-256) while they are read or staged, and a
declared hash is verified before anything is written. See
``docs/vector-conversion.md``.
"""

import logging
import os
import subprocess
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

import geopandas as gpd
import pandas as pd
import shapely
from obstore.store import ObjectStore

from csdr.geometries import add_geometry_id_name
from csdr.io import (
    exists,
    find_matching_files,
    get_store_with_prefix_from_url,
    split_path_and_file_name_from_url,
    write_gdf_to_parquet,
)
from csdr.utils import (
    PARTITION_PREFIX,
    CSDRException,
    download_with_sha256,
    is_remote_url,
    local_file_path,
    normalise_sha256,
    partition_parquet,
    sha256_file,
    url_suffix,
    verify_sha256,
)

logger = logging.getLogger(__name__)

OGR_SUFFIXES = (".shp", ".gpkg", ".geojson", ".json")
# GeoParquet sources; registers are often exported with the .geoparquet extension.
PARQUET_SUFFIXES = (".parquet", ".geoparquet")
SOURCE_SUFFIXES = (*OGR_SUFFIXES, *PARQUET_SUFFIXES, ".zip")
SHAPEFILE_SIDECARS = (".shx", ".dbf", ".prj", ".cpg")
LAYER_COLUMN = "layer"
MAKE_VALID_METHOD = (
    "make_valid(method='structure', keep_collapsed=False), polygonal parts kept "
    "for polygonal features"
)
POLYGONAL = ("Polygon", "MultiPolygon")

try:  # geopandas >= 1.0 prefers pyogrio; fall back to fiona if it is absent.
    import pyogrio  # noqa: F401

    READ_ENGINE = "pyogrio"
except ImportError:  # pragma: no cover - pyogrio ships with geopandas 1.x
    READ_ENGINE = "fiona"


def _get_geometry_id(geometry_id: str | None, dataset_url: str) -> str | None:
    """Default geometry id: the file name, kebab-cased, without extension."""
    if geometry_id is None:
        geometry_id = (
            split_path_and_file_name_from_url(dataset_url)[1]
            .replace(" ", "-")
            .lower()
            .split(".")[0]
        )
    return geometry_id


@dataclass
class StagedSource:
    """A local, readable copy (or the original path) of the source and its hashes."""

    path: Path
    sha256: str
    member_sha256: dict[str, str] = field(default_factory=dict)


@contextmanager
def stage_source(source: str) -> Iterator[StagedSource]:
    """Yield a local path for ``source`` with the SHA-256 of its bytes.

    Local files are read in place. Store URLs (``s3://``, ``https://``) are
    streamed to a temporary directory that is removed afterwards; credentials
    come from the store's usual chain. A bare ``.shp`` brings its sidecar files
    (``.shx``, ``.dbf``, ``.prj``, ``.cpg``); their hashes are recorded
    separately in ``member_sha256``. Prefer a zipped shapefile when one source
    hash should cover every member.
    """
    suffix = url_suffix(source)
    if suffix not in SOURCE_SUFFIXES:
        raise CSDRException(
            f"Unsupported vector source '{source}'. Supported extensions: "
            f"{', '.join(SOURCE_SUFFIXES)}. A directory or prefix is not a source."
        )
    if not is_remote_url(source):
        path = local_file_path(source)
        if not path.is_file():
            raise CSDRException(f"Source file does not exist: {source}")
        members = {}
        if suffix == ".shp":
            for sidecar in SHAPEFILE_SIDECARS:
                companion = path.with_suffix(sidecar)
                if companion.is_file():
                    members[companion.name] = sha256_file(companion)
        yield StagedSource(path, sha256_file(path), members)
        return

    directory_url, file_name = split_path_and_file_name_from_url(source)
    store = get_store_with_prefix_from_url(directory_url)
    with TemporaryDirectory(prefix="csdr-vector-") as directory:
        target = Path(directory) / file_name
        try:
            sha256 = download_with_sha256(store, file_name, target)
        except FileNotFoundError:
            raise CSDRException(f"Source file does not exist: {source}") from None
        members = {}
        if suffix == ".shp":
            stem = PurePosixPath(file_name).stem
            for sidecar in SHAPEFILE_SIDECARS:
                name = f"{stem}{sidecar}"
                try:
                    members[name] = download_with_sha256(
                        store, name, Path(directory) / name
                    )
                except FileNotFoundError:
                    continue
        yield StagedSource(target, sha256, members)


def zip_vector_members(zip_path: Path) -> list[str]:
    """Vector dataset members of a zip archive (shapefile, GeoPackage, GeoJSON)."""
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    return [
        name
        for name in names
        if url_suffix(name) in OGR_SUFFIXES
        and not name.endswith("/")
        and not name.startswith("__MACOSX/")
        and not PurePosixPath(name).name.startswith("._")
    ]


def resolve_zip_member(zip_path: Path, zip_member: str | None) -> str:
    """Pick the zip member to read: the one named, or the only vector member."""
    members = zip_vector_members(zip_path)
    if zip_member is not None:
        if zip_member not in members:
            raise CSDRException(
                f"Zip member '{zip_member}' is not a vector member of the archive. "
                f"Vector members: {members}"
            )
        return zip_member
    if len(members) != 1:
        raise CSDRException(
            "Name the zip member to read (--zip-member). The archive has "
            f"{len(members)} vector members: {members}"
        )
    return members[0]


def _list_layers(path: str) -> list[str]:
    if READ_ENGINE == "pyogrio":
        import pyogrio

        return [str(row[0]) for row in pyogrio.list_layers(path)]
    import fiona  # pragma: no cover

    return list(fiona.listlayers(path))  # pragma: no cover


def _read_ogr(path: str, layer: str | None, where: str | None) -> gpd.GeoDataFrame:
    layers = _list_layers(path)
    if layer is not None and layer not in layers:
        raise CSDRException(f"Layer '{layer}' not found. Layers: {layers}")
    if layer is None and len(layers) > 1:
        raise CSDRException(f"The source has several layers; choose one: {layers}")
    kwargs: dict[str, str] = {}
    if layer is not None:
        kwargs["layer"] = layer
    if where:
        kwargs["where"] = where
    return gpd.read_file(path, engine=READ_ENGINE, **kwargs)


def _read_parquet(path: str, where: str | None) -> gpd.GeoDataFrame:
    if not where:
        try:
            return gpd.read_parquet(path)
        except ValueError as error:
            raise CSDRException(
                f"The Parquet source is not GeoParquet (no geometry metadata): {error}"
            ) from None
    # GDAL wheels used here have no Parquet driver, so OGR SQL is not available
    # for Parquet. SedonaDB evaluates the same SQL WHERE syntax at read time.
    import sedona.db

    with TemporaryDirectory(prefix="csdr-vector-") as directory:
        if url_suffix(path) != ".parquet":
            # SedonaDB lists local files by the .parquet extension only.
            alias = Path(directory) / "source.parquet"
            alias.symlink_to(Path(path).resolve())
            path = str(alias)
        sd = sedona.db.connect()
        sd.read_parquet(path).to_view("source", overwrite=True)
        result = sd.sql(f"SELECT * FROM source WHERE {where}").to_pandas()
    if not isinstance(result, gpd.GeoDataFrame):
        raise CSDRException("The Parquet source has no geometry column.")
    return result


@dataclass
class ReadResult:
    gdf: gpd.GeoDataFrame
    sha256: str
    member_sha256: dict[str, str]
    zip_member: str | None
    where_engine: str | None


def read_vector_source(
    source: str,
    *,
    zip_member: str | None = None,
    layer: str | None = None,
    where: str | None = None,
) -> ReadResult:
    """Read ``source`` into a GeoDataFrame, filtering at read time when asked.

    ``where`` is an SQL WHERE clause. OGR formats evaluate it with OGR SQL
    (``where_engine`` ``ogr-sql``); GeoParquet with SedonaDB SQL
    (``sedonadb-sql``). Simple predicates (``=``, ``<>``, ``IN``, ``LIKE``,
    ``AND``/``OR``) mean the same in both.
    """
    with stage_source(source) as staged:
        suffix = url_suffix(str(staged.path))
        member = None
        if suffix in PARQUET_SUFFIXES:
            if layer is not None:
                raise CSDRException("--layer does not apply to a Parquet source.")
            gdf = _read_parquet(str(staged.path), where)
            engine = "sedonadb-sql" if where else None
        else:
            path = str(staged.path)
            if suffix == ".zip":
                member = resolve_zip_member(staged.path, zip_member)
                path = f"/vsizip/{staged.path.resolve()}/{member}"
            elif zip_member is not None:
                raise CSDRException("--zip-member applies only to a .zip source.")
            gdf = _read_ogr(path, layer, where)
            engine = "ogr-sql" if where else None
        return ReadResult(gdf, staged.sha256, staged.member_sha256, member, engine)


def _polygonal_only(geometry: object) -> object:
    """Polygonal parts of a geometry; line and point slivers become empty."""
    if geometry is None or geometry.geom_type in POLYGONAL:
        return geometry
    parts = [
        part for part in getattr(geometry, "geoms", []) if part.geom_type in POLYGONAL
    ]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def make_valid_polygonal(
    geometries: gpd.GeoSeries, polygonal: pd.Series
) -> gpd.GeoSeries:
    """``make_valid`` (structure method); rows flagged polygonal keep polygons only.

    ``polygonal`` marks rows whose feature was a Polygon or MultiPolygon before
    the operation (repair, reprojection or clipping), so a polygon feature never
    turns into a GeometryCollection with line or point slivers. Point and line
    features are left as ``make_valid`` returns them.
    """
    fixed = geometries.make_valid(method="structure", keep_collapsed=False)
    if polygonal.any():
        fixed[polygonal] = fixed[polygonal].apply(_polygonal_only)
    return fixed


def repair_geometries(
    gdf: gpd.GeoDataFrame, make_valid: bool
) -> tuple[gpd.GeoDataFrame, int]:
    """Repair invalid geometries in place of the originals; return the count found."""
    geometry = gdf.geometry
    invalid = ~geometry.isna() & ~geometry.is_empty & ~geometry.is_valid
    count = int(invalid.sum())
    if make_valid and count:
        gdf = gdf.copy()
        polygonal = geometry[invalid].geom_type.isin(POLYGONAL)
        gdf.loc[invalid, gdf.geometry.name] = make_valid_polygonal(
            geometry[invalid], polygonal
        )
    return gdf, count


def reproject(
    gdf: gpd.GeoDataFrame, epsg: int, make_valid: bool
) -> tuple[gpd.GeoDataFrame, int]:
    """Reproject, then repair geometries the reprojection made invalid."""
    if gdf.crs is not None and gdf.crs.to_epsg() == epsg:
        return gdf, 0
    return repair_geometries(gdf.to_crs(epsg), make_valid)


@dataclass
class GeometryFilter:
    """The union of a filter file's features, in the source CRS."""

    source: str
    sha256: str
    union: object


def load_geometry_filter(
    filter_source: str, target_crs: object, make_valid: bool = True
) -> GeometryFilter:
    """Read ``filter_source``, reproject it to ``target_crs`` and union its features."""
    if target_crs is None:
        raise CSDRException("--filter-geometry needs a source with a CRS.")
    read = read_vector_source(filter_source)
    features = read.gdf
    if features.crs is None:
        raise CSDRException("The --filter-geometry file has no CRS.")
    if features.crs != target_crs:
        features = features.to_crs(target_crs)
    # The filter itself is always repaired: an invalid union cannot be tested.
    features, _ = repair_geometries(features, True)
    union = features.geometry.union_all()
    if union is None or union.is_empty:
        raise CSDRException("The --filter-geometry file has no geometry.")
    shapely.prepare(union)
    return GeometryFilter(filter_source, read.sha256, union)


def apply_geometry_filter(
    gdf: gpd.GeoDataFrame, geometry_filter: GeometryFilter, clip: bool
) -> gpd.GeoDataFrame:
    """Keep rows intersecting the filter union; with ``clip``, cut them to it.

    Clipped geometries are repaired with :func:`make_valid_polygonal`; a
    polygon that only touches the filter (empty polygonal intersection) is
    dropped.
    """
    gdf = gdf[gdf.geometry.intersects(geometry_filter.union)]
    if clip and len(gdf):
        polygonal = gdf.geometry.geom_type.isin(POLYGONAL)
        clipped = gdf.geometry.intersection(geometry_filter.union)
        gdf = gdf.set_geometry(make_valid_polygonal(clipped, polygonal))
        gdf = gdf[~gdf.geometry.isna() & ~gdf.geometry.is_empty]
    return gdf


def write_pmtiles(
    gdf: gpd.GeoDataFrame,
    target_store: ObjectStore,
    file_name: str,
    fields_to_keep: list[str],
    make_valid: bool = True,
) -> None:
    """Write PMTiles with tippecanoe from an EPSG:4326 copy (zip-to-parquet flags)."""
    if gdf.crs is None:
        raise CSDRException("Cannot create PMTiles from a source without a CRS.")
    tiles = gdf[[*[f for f in fields_to_keep if f in gdf.columns], gdf.geometry.name]]
    tiles, _ = reproject(tiles, 4326, make_valid)
    with TemporaryDirectory() as directory:
        local_geojson = os.path.join(directory, "data.geojson")
        local_pmtiles = os.path.join(directory, "data.pmtiles")
        tiles.to_file(local_geojson, driver="GeoJSON")
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
        target_store.put(file_name, Path(local_pmtiles))
    logger.info(f"Created PMTiles file at {file_name}")


def partition_slugs(target_store: ObjectStore) -> set[str]:
    """Dataset slugs that own files under ``partition/`` (``<slug>_<lon>_<lat>``)."""
    try:
        files = find_matching_files(
            target_store, pattern=r"\.parquet$", prefix=PARTITION_PREFIX
        )
    except FileNotFoundError:
        return set()
    slugs = set()
    for path in files:
        stem = PurePosixPath(path).name.removesuffix(".parquet")
        parts = stem.rsplit("_", 2)
        slugs.add(parts[0] if len(parts) == 3 else stem)
    return slugs


@dataclass
class ConversionResult:
    """What a conversion read and wrote; the command records it in its step."""

    source: str
    source_sha256: str | None
    target_url: str
    member_sha256: dict[str, str] = field(default_factory=dict)
    zip_member: str | None = None
    where_engine: str | None = None
    skipped: bool = False
    row_count: int | None = None
    crs: str | None = None
    invalid_geometries: int | None = None
    geometries_repaired: int | None = None
    replaced_rows: int | None = None
    filter_geometry: str | None = None
    filter_geometry_sha256: str | None = None
    clip: bool | None = None
    rows_before_filter: int | None = None
    rows_after_filter: int | None = None
    reprojection_repairs: int = 0
    dropped_empty_geometries: int | None = None
    partition_rewritten: bool = False
    partition_count: int | None = None
    partition_prefix: str | None = None
    pmtiles_url: str | None = None


def _crs_label(gdf: gpd.GeoDataFrame) -> str | None:
    if gdf.crs is None:
        return None
    epsg = gdf.crs.to_epsg()
    return f"EPSG:{epsg}" if epsg is not None else gdf.crs.to_string()


def default_target_filename(source: str, zip_member: str | None = None) -> str:
    name = zip_member or split_path_and_file_name_from_url(source)[1]
    return PurePosixPath(name).stem


def convert_vector(
    source: str,
    target_location: str,
    *,
    target_filename: str | None = None,
    zip_member: str | None = None,
    layer: str | None = None,
    where: str | None = None,
    keep_columns: list[str] | None = None,
    name_field: str | None = None,
    geometry_id: str | None = None,
    layer_column_value: str | None = None,
    append: bool = False,
    filter_geometry: str | None = None,
    clip: bool = False,
    partition: bool = False,
    grid_chunks: int = 10,
    create_pmtiles: bool = False,
    pmtiles_fields: list[str] | None = None,
    source_sha256: str | None = None,
    make_valid: bool = True,
    overwrite: bool = True,
    drop_empty: bool = True,
) -> ConversionResult:
    """Convert one vector source; see the module docstring for the outputs.

    ``append`` (requires ``layer_column_value``) adds the converted rows to an
    existing ``<slug>.parquet``, first removing rows whose ``layer`` equals
    ``layer_column_value`` (so a retried conversion replaces its own rows),
    then rewrites the file and re-partitions it in full.

    ``filter_geometry`` keeps only rows intersecting the union of that file's
    features (reprojected to the source CRS); ``clip`` also cuts the kept
    geometries to the union.

    Partitions: one dataset per target folder. If ``partition/`` already holds
    this slug's files, they are always rewritten after a write (append or
    overwrite), even without ``partition``, so they never go stale. Partition
    files of another slug are refused.

    ``drop_empty`` removes null or empty geometries (after repair and clip)
    before writing and counts them.
    """
    declared_sha256 = normalise_sha256(source_sha256)
    if append and layer_column_value is None:
        raise CSDRException("--append needs --layer-column-value to identify rows.")
    if geometry_id is not None and name_field is None:
        raise CSDRException("--geometry-id needs --name-field.")
    if grid_chunks < 1:
        raise CSDRException("--grid-chunks must be at least 1.")
    if clip and filter_geometry is None:
        raise CSDRException("--clip is only valid with --filter-geometry.")

    target_location = target_location.rstrip("/")
    target_store = get_store_with_prefix_from_url(target_location)

    read = None
    if target_filename is None:
        if url_suffix(source) == ".zip" and zip_member is None:
            # The default name comes from the member, so resolve it first.
            read = read_vector_source(source, layer=layer, where=where)
            zip_member = read.zip_member
        target_filename = default_target_filename(source, zip_member)
    slug = target_filename.removesuffix(".parquet")
    parquet_file = f"{slug}.parquet"
    target_url = f"{target_location}/{parquet_file}"

    if exists(target_store, parquet_file) and not overwrite and not append:
        # The source is not read (or downloaded) when nothing will be written;
        # only a declared hash can be recorded.
        logger.info(
            f"Target {target_url} exists and overwrite is off; skipping conversion."
        )
        return ConversionResult(
            source=source,
            source_sha256=declared_sha256 if read is None else read.sha256,
            target_url=target_url,
            zip_member=zip_member,
            skipped=True,
        )

    # One dataset per target folder: the product engine reads all of partition/.
    existing_slugs = partition_slugs(target_store)
    other_slugs = sorted(existing_slugs - {slug})
    rewrite_partitions = slug in existing_slugs and not partition
    if other_slugs and (partition or slug in existing_slugs):
        raise CSDRException(
            "One dataset per target folder; found partitions for "
            f"{', '.join(other_slugs)} in {target_location}/{PARTITION_PREFIX}. "
            "Use a separate --target-location for this dataset."
        )

    if read is None:
        read = read_vector_source(
            source, zip_member=zip_member, layer=layer, where=where
        )
    verify_sha256(declared_sha256, read.sha256)
    gdf = read.gdf
    logger.info(f"Read {len(gdf)} features from {source}.")
    if len(gdf) == 0:
        raise CSDRException(
            "The source selection is empty. Check --where, --layer and the source."
        )

    if gdf.geometry.name != "geometry":
        gdf = gdf.rename_geometry("geometry")

    if keep_columns:
        extra = [name_field] if name_field else []
        wanted = list(dict.fromkeys([*keep_columns, *extra]))
        missing = [column for column in wanted if column not in gdf.columns]
        if missing:
            available = [c for c in gdf.columns if c != "geometry"]
            raise CSDRException(
                f"Columns not in the source: {missing}. Available: {available}"
            )
        gdf = gdf[[*wanted, "geometry"]]

    gdf, invalid_count = repair_geometries(gdf, make_valid)
    if invalid_count:
        action = "repaired with " + MAKE_VALID_METHOD if make_valid else "left as-is"
        logger.warning(f"{invalid_count} invalid geometries {action}.")

    geometry_filter, rows_before = None, None
    if filter_geometry is not None:
        geometry_filter = load_geometry_filter(filter_geometry, gdf.crs)
        rows_before = len(gdf)
        gdf = apply_geometry_filter(gdf, geometry_filter, clip)
        logger.info(
            f"--filter-geometry kept {len(gdf)} of {rows_before} features"
            f"{' (clipped)' if clip else ''}."
        )
        if len(gdf) == 0:
            raise CSDRException(
                "No source features intersect --filter-geometry; nothing to write."
            )
    rows_after = len(gdf) if geometry_filter is not None else None

    dropped_empty = None
    if drop_empty:
        empty = gdf.geometry.isna() | gdf.geometry.is_empty
        dropped_empty = int(empty.sum())
        if dropped_empty:
            logger.warning(
                f"Dropping {dropped_empty} features with null or empty geometry "
                "(after repair and clipping)."
            )
            gdf = gdf[~empty]
        if len(gdf) == 0:
            raise CSDRException("Every selected feature has a null or empty geometry.")

    if layer_column_value is not None:
        if LAYER_COLUMN in gdf.columns:
            raise CSDRException(
                f"The source already has a '{LAYER_COLUMN}' column; drop it with "
                "--keep-columns or do not set --layer-column-value."
            )
        gdf[LAYER_COLUMN] = layer_column_value

    if name_field is not None:
        gdf = add_geometry_id_name(
            gdf,
            name_field=name_field,
            geometry_id=_get_geometry_id(geometry_id, read.zip_member or source),
        )

    replaced = None
    if append and exists(target_store, parquet_file):
        existing = gpd.read_parquet(BytesIO(target_store.get(parquet_file).bytes()))
        if LAYER_COLUMN not in existing.columns:
            raise CSDRException(
                f"Cannot append: {target_url} has no '{LAYER_COLUMN}' column."
            )
        if existing.crs != gdf.crs:
            raise CSDRException(
                f"Cannot append: {target_url} is in {_crs_label(existing)}, the "
                f"source is in {_crs_label(gdf)}. Reproject one of them first."
            )
        same_layer = existing[LAYER_COLUMN] == layer_column_value
        replaced = int(same_layer.sum())
        if replaced:
            logger.info(
                f"Replacing {replaced} existing rows with layer '{layer_column_value}'."
            )
        gdf = gpd.GeoDataFrame(
            pd.concat([existing[~same_layer], gdf], ignore_index=True),
            geometry="geometry",
            crs=gdf.crs,
        )
    elif append:
        replaced = 0

    gdf = gdf.reset_index(drop=True)
    write_gdf_to_parquet(gdf, target_store, parquet_file)
    logger.info(f"Wrote {len(gdf)} features to {target_url}")

    result = ConversionResult(
        source=source,
        source_sha256=read.sha256,
        target_url=target_url,
        member_sha256=read.member_sha256,
        zip_member=read.zip_member,
        where_engine=read.where_engine,
        row_count=len(gdf),
        crs=_crs_label(gdf),
        invalid_geometries=invalid_count,
        geometries_repaired=invalid_count if make_valid else 0,
        replaced_rows=replaced,
        dropped_empty_geometries=dropped_empty,
    )
    if geometry_filter is not None:
        result.filter_geometry = geometry_filter.source
        result.filter_geometry_sha256 = geometry_filter.sha256
        result.clip = clip
        result.rows_before_filter = rows_before
        result.rows_after_filter = rows_after

    if rewrite_partitions:
        logger.warning(
            f"{target_location}/{PARTITION_PREFIX} already holds partitions for "
            f"'{slug}'; rewriting them so they match the new {parquet_file}."
        )
        result.partition_rewritten = True
    if partition or rewrite_partitions:
        partition_gdf, repairs = reproject(gdf, 4326, make_valid)
        result.reprojection_repairs += repairs
        result.partition_count = partition_parquet(
            target_store,
            partition_gdf,
            grid_chunks=grid_chunks,
            overwrite=True,
            file_prefix=slug,
        )
        result.partition_prefix = f"{target_location}/{PARTITION_PREFIX}"

    if create_pmtiles:
        pmtiles_file = f"{slug}.pmtiles"
        fields = (
            pmtiles_fields
            if pmtiles_fields is not None
            else ["csdr-id", "csdr-name", LAYER_COLUMN]
        )
        write_pmtiles(gdf, target_store, pmtiles_file, fields, make_valid)
        result.pmtiles_url = f"{target_location}/{pmtiles_file}"

    return result


def step_inputs(result: ConversionResult, **options: object) -> dict[str, object]:
    """Step ``inputs`` for a conversion: source identity plus the options used."""
    inputs: dict[str, object] = {"source": result.source}
    if result.source_sha256 is not None:
        inputs["source_sha256"] = result.source_sha256
    if result.member_sha256:
        inputs["source_member_sha256"] = result.member_sha256
    if result.zip_member is not None:
        inputs["zip_member"] = result.zip_member
    if result.where_engine is not None:
        inputs["where_engine"] = result.where_engine
    if result.filter_geometry is not None:
        inputs["filter_geometry"] = result.filter_geometry
        inputs["filter_geometry_sha256"] = result.filter_geometry_sha256
        inputs["clip"] = result.clip
        inputs["rows_before_filter"] = result.rows_before_filter
        inputs["rows_after_filter"] = result.rows_after_filter
    inputs.update({k: v for k, v in options.items() if v is not None})
    return inputs


def step_outputs(result: ConversionResult) -> dict[str, object]:
    outputs: dict[str, object] = {"target_url": result.target_url}
    if result.skipped:
        outputs["skipped"] = "target exists and overwrite is off"
        return outputs
    outputs["row_count"] = result.row_count
    outputs["crs"] = result.crs
    if result.replaced_rows is not None:
        outputs["replaced_rows"] = result.replaced_rows
    if result.dropped_empty_geometries is not None:
        outputs["dropped_empty_geometries"] = result.dropped_empty_geometries
    if result.partition_rewritten:
        outputs["partition_rewritten"] = True
    if result.partition_count is not None:
        outputs["partition_count"] = result.partition_count
        outputs["partition_prefix"] = result.partition_prefix
    if result.pmtiles_url is not None:
        outputs["pmtiles_url"] = result.pmtiles_url
    return outputs


def method_notes(result: ConversionResult, make_valid: bool) -> list[str]:
    """Human-readable method notes recorded with the step."""
    notes = []
    if result.skipped:
        return notes
    if result.invalid_geometries:
        if make_valid:
            notes.append(
                f"{result.invalid_geometries} invalid geometries repaired with "
                f"shapely {MAKE_VALID_METHOD}."
            )
        else:
            notes.append(
                f"{result.invalid_geometries} invalid geometries kept unrepaired "
                "(--no-make-valid)."
            )
    elif make_valid:
        notes.append(f"All geometries valid; {MAKE_VALID_METHOD} not needed.")
    if result.reprojection_repairs:
        notes.append(
            f"{result.reprojection_repairs} geometries made invalid by reprojection "
            f"to EPSG:4326 repaired with {MAKE_VALID_METHOD}."
        )
    if result.clip:
        notes.append(
            "Kept geometries clipped to the --filter-geometry union and repaired "
            f"with {MAKE_VALID_METHOD}."
        )
    if result.partition_count is not None:
        notes.append(
            "Partition files are an EPSG:4326 copy on a centroid grid; the main "
            "parquet keeps the native CRS."
        )
    if result.pmtiles_url is not None:
        notes.append("PMTiles built with tippecanoe from an EPSG:4326 copy.")
    return notes
