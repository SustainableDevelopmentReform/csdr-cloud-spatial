import json
import logging

import pandas as pd
import sedona.db
from odc.geo.geom import Geometry, box, unary_union
from odc.stac import parse_item
from pystac import ItemCollection
from python_retry import retry
from rustac import search_sync as rustac_search_sync
from xarray import Dataset

from csdr.indicators import (
    HANDLER_AREA_PERCENT,
    HANDLER_AREA_SUM,
    HANDLER_FEATURE_COUNT,
    HANDLER_ZONAL_STATISTICS,
    IndicatorFamily,
    family_for,
    order_indicators,
)
from csdr.io import split_path_and_file_name_from_url
from csdr.provenance import read_provenance
from csdr.utils import (
    CSDRException,
    PercentileMethod,
    ZonalCells,
    load_xarray_stacgeoparquet,
    xarray_calculate_area_m2,
    xarray_zonal_cells,
)

logger = logging.getLogger(__name__)


# The zonal statistics families refuse a geometry no STAC item covers: a
# statistic of no cells is undefined. (sum-<x>-area reports 0.0 instead.)
_NO_STAC_ITEMS_MESSAGE = (
    "No STAC items cover the requested geometry and time. "
    "Check the source coverage and reporting period before processing. "
    "Zonal statistics of missing source coverage are undefined."
)


def _search_stac_items(
    dataset_url: str,
    geometry_4326: Geometry,
    datetime_string_match: str | None = None,
) -> list[dict]:
    """STAC items of a STAC-GeoParquet that intersect the geometry and time."""
    geom_bbox = geometry_4326.boundingbox
    geom_bbox_list = [geom_bbox.left, geom_bbox.bottom, geom_bbox.right, geom_bbox.top]
    geom_geojson = geometry_4326.geojson(simplify=0)["geometry"]
    # TODO: Add to rustac_search_sync collections filter. use_duckdb?
    items = rustac_search_sync(
        dataset_url,
        bbox=geom_bbox_list,
        intersects=geom_geojson,
        datetime=datetime_string_match,
    )
    return list(items) if items else []


def _load_stac_band(
    items: ItemCollection,
    geometry_4326: Geometry,
    indicator: str,
    load_kwargs: dict,
) -> Dataset:
    """Load the items (chunked) over the geometry's bounding box and check the band."""
    # Force the use of Dask. Important for loading the xarray. Without chunking, large datasets may not fit into memory. Chunked (lazy, parallel) loading is scaleable.
    if load_kwargs.get("chunks") is None:
        load_kwargs["chunks"] = {}
    logger.info(f"Loading dataset with chunking settings: {load_kwargs.get('chunks')}")

    # Load the dataset as xarray from the STAC items. Filter spatially and temporally.
    data = load_xarray_stacgeoparquet(
        items,
        geometry_4326,
        **load_kwargs,
    )

    logger.info(f"Loaded data with shape {data.dims}")

    if indicator not in data.data_vars:
        raise CSDRException(
            f"Indicator {indicator} not found in dataset. Available: {list(data.data_vars)}"
        )
    return data


# The _get_area_m2_from_stac_geoparquet function does the following:
# 1. Loads a STAC-Geoparquet using rustac.
# 2. If items found, loads the xarray dataset from the STAC items.
# 4. Calculates the area where the specified indicators equals the given value/s within the geometry.
def _get_area_m2_from_stac_geoparquet(
    dataset_url: str,
    geometry_4326: Geometry,
    indicator: str,
    value_list: list[float] | None = None,
    datetime_string_match: str | None = None,
    load_kwargs: dict | None = None,
    raster_all_touched: bool = True,
) -> float:
    """Calculate the area of the dataset within the given geometry."""
    # Get the STAC items filtered by geometry and datetime
    if load_kwargs is None:
        load_kwargs = {}
    items = _search_stac_items(dataset_url, geometry_4326, datetime_string_match)

    if not items or len(items) == 0:
        # sum-<x>-area keeps reporting missing coverage as 0.0; the zonal
        # statistics families refuse it instead (_zonal_cells_for_parts).
        logger.warning(
            "No STAC items found for the given geometry and datetime filter. Returning area of 0.0"
        )
        return 0.0
    logger.info(
        f"Found {len(items)} STAC items for the given geometry and datetime filter."
    )

    data = _load_stac_band(ItemCollection(items), geometry_4326, indicator, load_kwargs)

    # Calculate area (m²). This also does the indicator/value/s filter.
    total_area_m2 = xarray_calculate_area_m2(
        data[indicator],
        geometry_4326,
        indicator=indicator,
        value_list=value_list,
        all_touched=raster_all_touched,
    )

    return total_area_m2


EXTENT_SOURCE_PROJ = "proj"
EXTENT_SOURCE_FOOTPRINT = "footprint"
EXTENT_SOURCE_NONE = "none"


def _raster_extent(items: ItemCollection) -> tuple[Geometry | None, str]:
    """Union of the source rasters' extents and where it came from.

    Each item's exact native grid (STAC projection fields, as odc-stac reads
    them) is used when present (``proj``), otherwise its footprint geometry
    (``footprint``, which can over-cover the raster). With an item that has
    neither, no extent is applied (``none``).
    """
    extents = []
    source = EXTENT_SOURCE_PROJ
    for item in items:
        geoboxes = parse_item(item).geoboxes()
        if geoboxes:
            extents.extend(geobox.extent for geobox in geoboxes)
        elif item.geometry is not None:
            extents.append(Geometry(item.geometry, crs="EPSG:4326"))
            source = EXTENT_SOURCE_FOOTPRINT
        else:
            return None, EXTENT_SOURCE_NONE
    if not extents:
        return None, EXTENT_SOURCE_NONE
    crs = extents[0].crs
    return unary_union([extent.to_crs(crs) for extent in extents]), source


def _get_zonal_cells_from_stac_geoparquet(
    dataset_url: str,
    geometry_4326: Geometry,
    indicator: str,
    *,
    datetime_string_match: str | None = None,
    load_kwargs: dict | None = None,
    raster_all_touched: bool = True,
    collect_values: bool = False,
    histogram: PercentileMethod | None = None,
    collect_distinct: bool = False,
) -> ZonalCells | None:
    """One pass over one band inside one geometry part; None when no item covers it.

    A part outside every source raster contributes no cells (it is outside the
    raster extent); the caller refuses a geometry none of whose parts is covered.
    """
    load_kwargs = dict(load_kwargs or {})
    items = _search_stac_items(dataset_url, geometry_4326, datetime_string_match)
    if not items:
        logger.info("No STAC items cover this geometry part.")
        return None
    logger.info(
        f"Found {len(items)} STAC items for the given geometry and datetime filter."
    )
    items = ItemCollection(items)
    data = _load_stac_band(items, geometry_4326, indicator, load_kwargs)
    extent, extent_source = _raster_extent(items)
    if extent_source != EXTENT_SOURCE_PROJ:
        logger.warning(
            f"STAC items for {indicator} lack projection fields; the source extent "
            f"is '{extent_source}' (item footprint or none), which can count fill "
            "cells outside the raster in the valid-fraction denominator."
        )
    cells = xarray_zonal_cells(
        data[indicator],
        geometry_4326,
        all_touched=raster_all_touched,
        extent=extent,
        collect_values=collect_values,
        histogram=histogram,
        collect_distinct=collect_distinct,
    )
    cells.extent_source = extent_source
    return cells


def _get_zonal_cells_from_dataset_geometry(
    dataset_url: str,
    dataset_type: str,
    geometry_4326: Geometry,
    indicator: str,
    **kwargs: object,
) -> ZonalCells | None:
    """Zonal cell populations for one geometry part (raster datasets only)."""
    if dataset_type == "stac-geoparquet":
        return _get_zonal_cells_from_stac_geoparquet(
            dataset_url, geometry_4326, indicator, **kwargs
        )
    raise CSDRException(
        "Zonal statistics (mean, percentiles, valid fraction, distinct count) need a "
        f"'stac-geoparquet' raster dataset; got '{dataset_type}'."
    )


# TODO: Generalise this to work for any geoparquet dataset, not just ACA reef.
def _get_area_m2_from_geoparquet_sedona(
    sd: sedona.db.context.SedonaContext,
    dataset_url: str,
    geometry_4326_wkt: str,
    indicator: str | None = None,
    value_list: list[float] | None = None,
    datetime_string_match: str | None = None,
) -> float:
    # This should already handle the bbox intersection optimization internally
    # This does predicate pushdown and spatial filtering using Sedona rather than loading everything into memory
    # Local for development testing
    # url can be s3://, https://, or local.

    # TODO: Add filters for indicator, value_list, and datetime_string_match

    # TODO: Add S3 Authentication using Boto3CredentialProvider. Can pass aws.access_key_id and aws.secret_access_key to Sedona.
    region = "ap-southeast-2"  # TODO: Get this from env/config.

    # TODO: Add S3 Authentication using Boto3CredentialProvider. Can pass aws.access_key_id and aws.secret_access_key to Sedona.
    sd.read_parquet(
        dataset_url, options={"aws.skip_signature": True, "aws.region": region}
    ).to_view("dataset", overwrite=True)

    area_result_m2 = sd.sql(
        f"""
        SELECT SUM(ST_Area(ST_Transform(geometry, 6933))) AS total_area_m2
        FROM dataset
        WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{geometry_4326_wkt}'), 4326))
        """
    ).to_pandas()

    area_m2 = area_result_m2["total_area_m2"][0]
    if pd.isna(area_m2):
        logger.info("No intersected dataset geometries found.")
        return 0.0
    else:
        logger.info(f"Total intersected area: {area_m2:.2f} m²")

    return round(float(area_m2), 2)


# TODO: Generalise this to work for any geoparquet dataset, not just VIDA Buildings.
def _get_count_points_in_polygon_geoparquet(
    sd: sedona.db.context.SedonaContext,
    dataset_url: str,
    geometry_4326_wkt: str,
) -> int:
    # buildings.parquet is in EPSG:4326.

    # This is for Source.coop data - VIDA Buildings dataset.
    # Buildings steps:
    # 1. Use sedona to intersect with buildings.parquet index file that we make.
    # 2. Then know which country 2nd level admin area parquets to load based on that intersection. Use sedona again to load only those parquets.
    # 3. Calculate count of buildings from those parquets.

    # EPSG:4326
    sd.read_parquet(dataset_url).to_view("index_data", overwrite=True)

    intersected_partition_urls = sd.sql(
        f"""
        SELECT
            country_code,
            s2_code,
            url,
            geometry
        FROM index_data
        WHERE ST_Intersects(
            geometry,
            ST_SetSRID(ST_GeomFromText('{geometry_4326_wkt}'), 4326)
        );
        """
    ).to_pandas()

    if intersected_partition_urls.empty:
        logger.info("No intersected dataset geometries found in index.")
        return 0
    logger.info(
        f"Found {len(intersected_partition_urls)} intersected 2nd level country admin area parquet files from index."
    )

    total_count = 0

    # Retry on failure. This prevents the whole pod from rerunning, when just 1 or 2 of hundreds of requests fail because of Source Coop proxy.
    @retry(max_retries=3, retry_logger=logger)
    def count_points_parquet(row: pd.Series) -> int:
        country_code = row["country_code"]
        s2_code = row["s2_code"]
        partition_url = row["url"]
        try:
            sd.read_parquet(partition_url).to_view("data", overwrite=True)
            count_result = sd.sql(
                f"""
                SELECT COUNT(*) AS geom_count
                FROM data
                WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{geometry_4326_wkt}'), 4326))
                """
            ).to_pandas()
            geom_count = count_result["geom_count"][0]
            if not pd.isna(geom_count):
                logger.info(f"{geom_count} buildings for parquet.")
                return int(geom_count)
            return 0
        except Exception:
            logger.exception(
                f"Error processing 2nd level country admin area parquet {country_code}/{s2_code}"
            )
            raise

    for idx, (_, row) in enumerate(intersected_partition_urls.iterrows(), 1):
        try:
            logger.info(
                f"Processing parquet {idx} of {len(intersected_partition_urls)}. Reading parquet: '{row.country_code}', '{row.s2_code}', '{row.url}'"
            )
            total_count += count_points_parquet(row)
        except Exception:
            logger.exception(
                f"Failed to process 2nd level country admin area parquet {row.country_code}/{row.s2_code} after retries. Raising so workflow will retry."
            )
            raise

    return int(total_count)


def _get_area_m2_from_dataset_geometry(
    sd: sedona.db.context.SedonaContext,
    dataset_url: str,
    dataset_type: str,
    geometry_4326: Geometry,
    indicator: str,
    value_list: list[float] | None = None,
    datetime_string_match: str | None = None,
    load_kwargs: dict | None = None,
    raster_all_touched: bool = True,
) -> float:
    """Calculate the area (m²) of the dataset within the given geometry."""

    if load_kwargs is None:
        load_kwargs = {}
    if dataset_type == "stac-geoparquet":
        return _get_area_m2_from_stac_geoparquet(
            dataset_url,
            geometry_4326,
            indicator,
            value_list,
            datetime_string_match=datetime_string_match,
            load_kwargs=load_kwargs,
            raster_all_touched=raster_all_touched,
        )
    elif dataset_type == "geoparquet":
        # This path config is specific to the the partitioned ACA reef geoparquet structure.
        path, _file_name = split_path_and_file_name_from_url(dataset_url)
        partition_path = f"{path}/partition/"  # Needs trailing slash for Sedona to read all files in the partition folder
        return _get_area_m2_from_geoparquet_sedona(
            sd,
            partition_path,
            geometry_4326.wkt,
            indicator,
            value_list,
            datetime_string_match=datetime_string_match,
        )
    else:
        raise CSDRException(
            f"Unsupported dataset type: {dataset_type}. Only 'stac-geoparquet' and 'geoparquet' are supported."
        )


# Constants
_MAX_GEOM_AREA_KM2 = (
    500_000  # ~Ivory Coast EEZ size — geometries larger than this get tiled
)
# Smaller tiles = less memory usage but more tiles to process.
# Larger tiles = more memory usage but fewer tiles to process.
_TILE_SIDE_M = 500_000  # ~250,000 km² tiles


def _tile_geometry(geom: Geometry) -> list[Geometry]:
    """
    Split a large geometry into tiles for memory-efficient processing.
    Geometries under _MAX_GEOM_AREA_KM2 are returned as-is.
    Tiles are clipped to the original geometry — empty tiles are discarded.
    Coordinates are in EPSG:6933 (metres) for area calculation and tiling,
    then returned in the original CRS.
    """
    geom_6933 = geom.to_crs("EPSG:6933")
    geom_area_km2 = geom_6933.area / 1_000_000

    if geom_area_km2 <= _MAX_GEOM_AREA_KM2:
        logger.info(
            f"Geometry area {geom_area_km2:.0f} km² is under limit, tiling not needed."
        )
        return [geom]

    bbox = geom_6933.boundingbox  # BoundingBox with .left .right .top .bottom in metres
    width_m = bbox.right - bbox.left
    height_m = bbox.top - bbox.bottom
    x_tiles = int(width_m // _TILE_SIDE_M) + 1
    y_tiles = int(height_m // _TILE_SIDE_M) + 1

    logger.info(
        f"Geometry area {geom_area_km2:.0f} km² exceeds {_MAX_GEOM_AREA_KM2} km² limit. "
        f"Tiling into {x_tiles}x{y_tiles} = {x_tiles * y_tiles} tiles..."
    )

    tiles = []
    for i in range(x_tiles):
        for j in range(y_tiles):
            tile_minx = bbox.left + i * _TILE_SIDE_M
            tile_miny = bbox.bottom + j * _TILE_SIDE_M
            tile_maxx = min(tile_minx + _TILE_SIDE_M, bbox.right)
            tile_maxy = min(tile_miny + _TILE_SIDE_M, bbox.top)

            tile_geom_6933 = box(
                tile_minx, tile_miny, tile_maxx, tile_maxy, crs="EPSG:6933"
            )
            tile_geom = tile_geom_6933.to_crs(geom.crs)
            # Clip tiles to geom boundary.
            clipped = geom.intersection(tile_geom)
            if clipped is not None and not clipped.is_empty:
                tiles.append(clipped)

    logger.info(f"Tiling produced {len(tiles)} tiles.")
    return tiles


def _tile_geometries(geoms: list[Geometry]) -> list[Geometry]:
    """Tile any oversized geometries in a list, returning the full set ready for processing."""
    logger.info(f"Before tiling: {len(geoms)} geometries.")
    tiled = [tile for geom in geoms for tile in _tile_geometry(geom)]
    logger.info(f"After tiling: {len(tiled)} geometries.")
    return tiled


def process_indicators_for_geometry(
    geometry_4326: Geometry,
    indicators: dict[str, dict],
    dataset_provenance_url: str,
    datetime_string_match: str | None = None,
    load_kwargs: dict | None = None,
    raster_all_touched: bool = True,
    percentile_method: PercentileMethod | None = None,
    zonal_metadata: dict | None = None,
) -> dict[str, str | float]:
    if load_kwargs is None:
        load_kwargs = {}
    if percentile_method is None:
        percentile_method = PercentileMethod()
    results = {}

    # Resolve every indicator to a registered family with a handler before any
    # source is read (csdr.indicators). Declared families validate on the CLI but
    # have no handler in this build.
    handlers = {}
    families = {}
    for key in indicators:
        family = family_for(key)
        if family is None:
            raise CSDRException(
                f"Indicator {key} matches no indicator family, so no handler can compute it."
            )
        if not family.implemented:
            raise CSDRException(
                f"indicator family {family.template} is registered but has no handler in this build"
            )
        handlers[key] = family.handler
        families[key] = family
    zonal_plan = _zonal_statistics_plan(indicators, families, percentile_method)

    sd = sedona.db.connect()

    logger.info(f"Loading dataset from {dataset_provenance_url}")
    provenance = read_provenance(dataset_provenance_url)
    dataset_url = provenance.get("dataUrl")
    dataset_type = provenance.get("dataType")

    # Dependent families (percent-<x>-area needs sum-<x>-area) run after the
    # family they depend on; otherwise by id.
    indicators = order_indicators(indicators)

    if zonal_plan and dataset_type != "stac-geoparquet":
        raise CSDRException(
            "Zonal statistics (mean, percentiles, valid fraction, distinct count) need a "
            f"'stac-geoparquet' raster dataset; got '{dataset_type}'."
        )

    # Every statistic of one band comes from one pass over the geometry's parts.
    zonal_cells_by_band: dict[str, ZonalCells] = {}

    for var_key, var_info in indicators.items():
        indicator_name = var_info.get("indicator-name")
        indicator_value_s = var_info.get("indicator-value")

        indicator_value_list = None

        # Indicator value can be a single string, a single float, or a comma-separated list of strings or floats.
        if indicator_value_s is None:  # No indicator value provided, set to None.
            pass
        elif isinstance(
            indicator_value_s, str
        ):  # Split comma-seperated string to list, and parse to floats.
            iv_list = indicator_value_s.strip().split(",")
            indicator_value_list = []
            for iv in iv_list:
                iv = iv.strip()
                try:
                    iv_float = float(iv)
                    iv = iv_float
                    indicator_value_list.append(iv_float)
                except ValueError:
                    indicator_value_list.append(
                        iv
                    )  # Not parsable to float, keep as string.
        else:  # Else just try to parse it to float if it's not already.
            try:
                iv_float = float(indicator_value_s)
                indicator_value_s = iv_float
                indicator_value_list = [indicator_value_s]
            except ValueError:
                raise CSDRException(
                    f"Indicator value {indicator_value_s} is not parsable to float. It should be either a single float, or a comma-separated list of strings or floats."
                )

        logger.info(
            f"Processing indicators: {var_key} with indicator name: {indicator_name} and value/s: {indicator_value_s}"
        )

        # Explode multipolygon geometries to single polygons
        geoms = [geometry_4326]
        if geometry_4326.geom_type == "MultiPolygon":
            geoms = list(geometry_4326.geoms)

        # If any geom is over a certain size, tile it into smaller pieces. Without this, we cannot run Indonesia EEZ GMW v4 at full resolution for example.
        # For example this makes Australia's 8 geometries into 86.
        geoms_tiled = _tile_geometries(geoms)

        if handlers[var_key] == HANDLER_ZONAL_STATISTICS:
            cells = zonal_cells_by_band.get(indicator_name)
            if cells is None:
                cells = _zonal_cells_for_parts(
                    dataset_url,
                    dataset_type,
                    geoms_tiled,
                    indicator_name,
                    zonal_plan[indicator_name],
                    datetime_string_match=datetime_string_match,
                    load_kwargs=load_kwargs,
                    raster_all_touched=raster_all_touched,
                )
                zonal_cells_by_band[indicator_name] = cells
            results[var_key] = _zonal_statistic(
                families[var_key], cells, indicator_value_list, percentile_method
            )
            logger.info(f"Calculated {var_key}: {results[var_key]}")
            continue

        # These total_* indicators are the sums over all single geometries in the multipolygon
        # TODO: When doing the indicator refactor, generalise these.
        total_multipolygon_area_m2 = 0.0  # Need for percent area calculations
        total_indicator_area_m2 = 0.0
        total_count = 0
        for i, geom_4326 in enumerate(geoms_tiled):
            logger.info(f"Processing geom {i + 1} of {len(geoms_tiled)}")
            # For percent area calculations, we need the total area of the multipolygon in m²
            geometry_6933 = geom_4326.to_crs("EPSG:6933")
            geom_area_m2 = geometry_6933.area
            total_multipolygon_area_m2 += geom_area_m2
            # Area indicators
            if handlers[var_key] == HANDLER_AREA_SUM:  # sum-<x>-area
                area_m2 = _get_area_m2_from_dataset_geometry(
                    sd,
                    dataset_url,
                    dataset_type,
                    geom_4326,
                    datetime_string_match=datetime_string_match,
                    indicator=indicator_name,
                    value_list=indicator_value_list,
                    load_kwargs=load_kwargs,
                    raster_all_touched=raster_all_touched,
                )
                total_indicator_area_m2 += area_m2
                results[var_key] = total_indicator_area_m2
                logger.info(
                    f"Total area by value: {total_indicator_area_m2} m² for indicator {var_key}, value/s {indicator_value_s}"
                )
            elif handlers[var_key] == HANDLER_FEATURE_COUNT:  # count-<x>
                logger.info("Starting count indicator analysis...")
                # TODO: Try to parallelise this to improve performance on multipolygons with many parts, that each intersect many parquet files.
                count = _get_count_points_in_polygon_geoparquet(
                    sd, dataset_url, geom_4326.wkt
                )
                total_count += count
                results[var_key] = total_count
                logger.info(
                    f"Total count of intersected buildings for this multipolygon geometry so far: {total_count}"
                )

        # Handle area percent indicators outside of the single geometry loop, since they depend on total area calculations
        if handlers[var_key] == HANDLER_AREA_PERCENT:  # percent-<x>-area
            logger.info(
                "Calculating percent area now that all geoms have been processed..."
            )
            indicator_area_m2 = results.get(
                f"sum-{var_key.replace('percent-', '')}", 0.0
            )
            area_percent = (
                (indicator_area_m2 / total_multipolygon_area_m2) * 100.0
                if total_multipolygon_area_m2 > 0
                else 0.0
            )
            results[var_key] = area_percent
            logger.info(
                f"Calculated {var_key}: {area_percent:.2f} % (Indicator area: {indicator_area_m2:.2f} m², Total geom area: {total_multipolygon_area_m2:.2f} m²)"
            )

    if zonal_metadata is not None and zonal_cells_by_band:
        zonal_metadata.update(
            _zonal_statistics_metadata(
                indicators,
                families,
                zonal_cells_by_band,
                percentile_method,
                raster_all_touched,
            )
        )

    return results


# Zonal statistics families: the statistic each family computes from ZonalCells.
_PERCENTILE_FAMILIES = {"p10": 0.1, "p25": 0.25, "p50": 0.5, "p75": 0.75, "p90": 0.9}
_ZONAL_FAMILIES = {"mean", "valid-fraction", "distinct-count", *_PERCENTILE_FAMILIES}


def _zonal_statistics_plan(
    indicators: dict[str, dict],
    families: dict[str, IndicatorFamily],
    percentile_method: PercentileMethod,
) -> dict[str, dict]:
    """What one pass per band must collect, validated before any source is read."""
    plan: dict[str, dict] = {}
    for key, info in indicators.items():
        family = families[key]
        if family.handler != HANDLER_ZONAL_STATISTICS:
            continue
        if family.name not in _ZONAL_FAMILIES:
            raise CSDRException(f"No zonal statistic is defined for {family.template}.")
        band = info.get("indicator-name")
        if not band:
            raise CSDRException(
                f"{key} needs an indicator-name: the raster band (asset key) to summarise."
            )
        value = info.get("indicator-value")
        if value is not None and family.name != "distinct-count":
            raise CSDRException(
                f"{key}: indicator-value applies only to distinct-<x>-count. Means, "
                "percentiles and valid fractions use every valid cell of the band."
            )
        if value is not None and not _is_numeric_value_list(value):
            raise CSDRException(
                f"{key}: indicator-value must be a number or a comma-separated list "
                f"of numbers (raster class values); got {value!r}."
            )
        needs = plan.setdefault(
            band,
            {"collect_values": False, "histogram": None, "collect_distinct": False},
        )
        if family.name in _PERCENTILE_FAMILIES:
            if percentile_method.is_histogram:
                needs["histogram"] = percentile_method
            else:
                needs["collect_values"] = True
        elif family.name == "distinct-count":
            needs["collect_distinct"] = True
    return plan


def _is_numeric_value_list(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    if not isinstance(value, str):
        return False
    try:
        [float(part) for part in value.split(",")]
    except ValueError:
        return False
    return True


def _zonal_cells_for_parts(
    dataset_url: str,
    dataset_type: str,
    parts: list[Geometry],
    indicator: str,
    needs: dict,
    *,
    datetime_string_match: str | None,
    load_kwargs: dict,
    raster_all_touched: bool,
) -> ZonalCells:
    """Combine every part's cells into one population (the union of the parts).

    Counts, sums, histograms and distinct values add up exactly; valid values are
    concatenated for linear percentiles. Parts do not overlap, so with the
    cell-centre rule each cell belongs to at most one part. With all-touched, a
    cell touched by two parts is counted in both, as the area sum does.
    """
    combined = ZonalCells()
    covered_parts = 0
    for i, part in enumerate(parts):
        logger.info(f"Zonal statistics for {indicator}: part {i + 1} of {len(parts)}")
        cells = _get_zonal_cells_from_dataset_geometry(
            dataset_url,
            dataset_type,
            part,
            indicator,
            datetime_string_match=datetime_string_match,
            load_kwargs=load_kwargs,
            raster_all_touched=raster_all_touched,
            **needs,
        )
        if cells is None:
            continue
        covered_parts += 1
        combined.merge(cells)
    # Unlike sum-<x>-area, which reports missing coverage as 0.0, the zonal
    # families refuse a boundary that no item covers or that has no valid cell.
    if covered_parts == 0:
        raise CSDRException(_NO_STAC_ITEMS_MESSAGE)
    # Support is checked over the whole boundary before any statistic.
    combined.require_support()
    return combined


def _zonal_statistic(
    family: IndicatorFamily,
    cells: ZonalCells,
    value_list: list | None,
    percentile_method: PercentileMethod,
) -> float | int:
    if family.name == "mean":
        return cells.mean()
    if family.name == "valid-fraction":
        return cells.valid_fraction()
    if family.name in _PERCENTILE_FAMILIES:
        return cells.percentile(_PERCENTILE_FAMILIES[family.name], percentile_method)
    if family.name == "distinct-count":
        return cells.distinct_count(value_list)
    raise CSDRException(f"No zonal statistic is defined for {family.template}.")


def _zonal_statistics_metadata(
    indicators: dict[str, dict],
    families: dict[str, IndicatorFamily],
    cells_by_band: dict[str, ZonalCells],
    percentile_method: PercentileMethod,
    raster_all_touched: bool,
) -> dict:
    """Method parameters and cell counts recorded in the per-geometry metadata."""
    metadata: dict = {
        "rasterCellRule": "all-touched" if raster_all_touched else "cell-centre",
        "zonalCellCounts": {
            band: cells.cell_counts() for band, cells in cells_by_band.items()
        },
    }
    if any(families[key].name in _PERCENTILE_FAMILIES for key in indicators):
        metadata.update(percentile_method.metadata())
    return metadata


# Run-level keys that process-geometry writes on every row. They are already in
# the product run's provenance, so they are not posted again with each output
# (the full command line and store paths would inflate every posted value).
RUN_LEVEL_METADATA_KEYS = frozenset(
    ("command", "geometryProvenanceUrl", "datasetProvenanceUrl")
)


def _metadata_json_value(value: object) -> object:
    # Parquet structs read through pandas can contain NumPy arrays/scalars.
    # Convert them without rounding coordinates or other captured evidence.
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError("Product metadata must contain JSON-compatible values.")


def parse_outputs(df: pd.DataFrame) -> dict:
    """Group a consolidated product's rows by indicator and time point.

    Each output carries the row's ``metadata`` (method parameters and cell
    counts from process-geometry), converted to plain JSON values, without the
    run-level keys in ``RUN_LEVEL_METADATA_KEYS``; when nothing else remains
    the output has no ``metadata`` key, as before. Absent
    values (None or NaN) are skipped: Parquet unions struct fields across
    rows, so an indicator missing from one row reads back as null, which is
    not an observed value and which the app rejects.
    """
    outputs = {}

    for _, row in df.iterrows():
        timePoint = row["timePoint"]
        metadata = None
        if isinstance(row.get("metadata"), dict):
            kept = {
                key: value
                for key, value in row["metadata"].items()
                if key not in RUN_LEVEL_METADATA_KEYS
            }
            if kept:
                metadata = json.loads(
                    json.dumps(kept, default=_metadata_json_value, allow_nan=False)
                )
        for indicator, value in row["indicators"].items():
            if value is None or pd.isna(value):
                continue
            if indicator not in outputs:
                outputs[indicator] = {}
            output = {"geometryOutputId": row["geometryOutputId"], "value": value}
            if metadata is not None:
                # A copy per output, so callers can edit one without the others.
                output["metadata"] = json.loads(json.dumps(metadata))

            if timePoint not in outputs[indicator]:
                outputs[indicator][timePoint] = []

            outputs[indicator][timePoint].append(output)

    return outputs
