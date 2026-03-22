import logging
import re

import pandas as pd
import sedona.db
from odc.geo import mask
from odc.geo.geom import Geometry, box
from pystac import ItemCollection
from python_retry import retry
from rustac import search_sync as rustac_search_sync

from csdr.io import split_path_and_file_name_from_url
from csdr.provenance import read_provenance
from csdr.utils import (
    CSDRException,
)

logger = logging.getLogger(__name__)


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
    load_kwargs: dict = {},
) -> float:
    """Calculate the area of the dataset within the given geometry."""
    # Get the STAC items filtered by geometry and datetime
    # geom_bbox_4326 = geometry_4326.boundingbox
    # geom_bbox_4326_list = [
    #     geom_bbox_4326.left,
    #     geom_bbox_4326.bottom,
    #     geom_bbox_4326.right,
    #     geom_bbox_4326.top,
    # ]
    geom_geojson = geometry_4326.geojson(simplify=0)["geometry"]
    # TODO: Add to rustac_search_sync collections filter. use_duckdb?
    items = rustac_search_sync(
        dataset_url,
        # bbox=geom_bbox_4326_list, # Why add bbox? Surely intersects is enough?
        intersects=geom_geojson,
        datetime=datetime_string_match,
    )

    if not items or len(items) == 0:
        logger.info(
            "No STAC items found for the given geometry and datetime filter. Returning area of 0.0"
        )
        return 0.0
    logger.info(
        f"Found {len(items)} STAC items for the given geometry and datetime filter."
    )

    items = ItemCollection(items)
    # TODO: Don't just skip these, do a seperate area calc after "antimeridian fixing them".
    items_cleaned = []
    naughty_antimeridian_items = []
    # print(items[0].assets)
    print(f"Before cleaning, {len(items)} STAC items found for loading.")
    for item in list(items):
        minx, _miny, maxx, _maxy = item.bbox
        if (minx < -180 or maxx > 180) or (minx == -180 and maxx == 180):
            # logging.warning(
            #     f"##################### Item {item.id} has bbox that spans the antimeridian: {item.bbox}. Skipping for now."
            # )
            naughty_antimeridian_items.append(item)
        else:
            # logging.info("Item bbox ok.")
            items_cleaned.append(item)
        # if item.id == "dep_s2_seagrass_066_019_2017":
        #     print(item.assets.get(indicator, {}.get('href')))
        #     print(item.bbox)
        #     import pdb; pdb.set_trace()
    items = ItemCollection(items_cleaned)
    logger.info(f"After cleaning, {len(items)} STAC items remain for loading.")

    logger.warning(
        f"Hit {len(naughty_antimeridian_items)} naughty antimeridian items: {[item.id for item in naughty_antimeridian_items]}"
    )

    # The problem is that dep_s2_seagrass_066_019_2017_seagrass spans the antimeridian (when reprojected from native 3832 to 6933 at 10m).
    # This causes the loaded dataset to be insanely big. For some reason we don't hit this for GMW or DEPSeagrass at 100m. Maybe because they are in 4326.

    # Force the use of Dask. Important for loading the xarray. Without chunking, large datasets may not fit into memory. Chunked (lazy, parallel) loading is scaleable.
    if load_kwargs.get("chunks") is None:
        load_kwargs["chunks"] = {}
    logger.info(f"Loading dataset with chunking settings: {load_kwargs.get('chunks')}")

    """
    # Load the dataset as xarray from the STAC items.
    data = load_xarray_stacgeoparquet(
        items,
        **load_kwargs,
    )

    logger.info(f"Loaded data with shape {data.dims}")
    assert data.sizes["x"] < 1_000_000, "Error: X dimension spanning world."

    # After loading, before any compute, check antimeridian issue. This happens for Fiji.
    max_pixel_width = 500_000
    x_size = data.sizes["x"]
    if x_size > max_pixel_width:
        raise CSDRException(
            f"Array shape (y={data.sizes['y']}, x={x_size}) indicates antimeridian issue. Maximum allowed is {max_pixel_width}."
        )

    if indicator not in data.data_vars:
        raise CSDRException(
            f"Indicator {indicator} not found in dataset. Available: {list(data.data_vars)}"
        )

    # Calculate area (m²). This also does the indicator/value/s filter.
    total_area_m2 = xarray_calculate_area_m2(
        data[indicator], geometry_4326, indicator=indicator, value_list=value_list
    )

    logger.info(f"Total area calculated: {total_area_m2} m²")

    return total_area_m2
    """

    def calculate_area_antimeridian_items(
        antimeridian_items: list,
        geom_4326: Geometry,
        indicator: str,
        value_list: list[float] | None = None,
    ) -> float:
        from odc.stac import load
        from pyproj import Transformer

        total_count = 0

        for item in antimeridian_items:
            # Load in native 3832
            data = load(
                [item],
                crs="EPSG:3832",
                # TODO: replace with load_kwargs.
                # **load_kwargs,
                resolution=10,
                chunks={},  # Lazy
            )[indicator]

            # Filter values
            if value_list is not None:
                data = data.where(data.isin(value_list))

            # Mask to geometry in native 3832 — keeps dataset tiny
            geom_3832 = geom_4326.to_crs("EPSG:3832")
            data = mask(data, geom_3832)

            # CLIP the array extent to only the valid region — shrinks the dask array itself
            bb = geom_3832.boundingbox
            data = data.sel(
                x=slice(bb.left, bb.right),
                y=slice(bb.top, bb.bottom),  # descending y
            )
            # logger.info(f"Geom bbox: {bb}")
            logger.info(f"Array shape after clip: {data.sizes['x']}, {data.sizes['y']}")
            logger.info(
                f"Pixels after native mask+clip: {int(data.notnull().sum().compute())}"
            )

            # Find antimeridian x in 3832 metres
            t = Transformer.from_crs(4326, 3832, always_xy=True)
            antimeridian_x, _ = t.transform(180, 0)

            # Split at antimeridian in 3832 space
            west = data.sel(x=data.x[data.x < antimeridian_x])
            east = data.sel(x=data.x[data.x >= antimeridian_x])

            west_pixel_count = int(west.notnull().sum().compute())
            east_pixel_count = int(east.notnull().sum().compute())

            logger.info(
                f"West part shape: {west.sizes['x']}, {west.sizes['y']}, pixels: {west_pixel_count}"
            )
            logger.info(
                f"East part shape: {east.sizes['x']}, {east.sizes['y']}, pixels: {east_pixel_count}"
            )

            logger.info("Reprojecting parts to 6933...")

            # Reproject clipped data directly — already small enough after clip
            logger.info(f"Reprojecting clipped data {dict(data.sizes)} to 6933...")
            data_6933 = data.rio.reproject("EPSG:6933", resolution=10)
            logger.info(f"Shape after reproject: {dict(data_6933.sizes)}")

            # Mask to geometry in 6933
            geom_6933 = geom_4326.to_crs("EPSG:6933")
            masked = mask(data_6933, geom_6933)

            count = int(masked.notnull().sum().compute())
            logger.info(f"Item {item.id} pixel count: {count}")
            total_count += count

        pixel_area_m2 = 10 * 10  # 6933 equal area, 10m resolution
        logger.info(
            f"Total pixel count across antimeridian items: {total_count}, which is approximately {total_count * pixel_area_m2:.2f} m²"
        )
        return round(total_count * pixel_area_m2, 2)

    naughty_area = calculate_area_antimeridian_items(
        naughty_antimeridian_items, geometry_4326, indicator, value_list
    )
    return naughty_area


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
        logger.info(f"Total intersected area: {area_m2:.2f}m²")

    return round(float(area_m2), 2)


# TODO: Generalise this to work for any geoparquet dataset, not just VIDA Buildings.
def _get_count_points_in_polygon_geoparquet(
    sd: sedona.db.context.SedonaContext,
    dataset_url: str,
    geometry_wkt: str,
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
            ST_SetSRID(ST_GeomFromText('{geometry_wkt}'), 4326)
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
                WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{geometry_wkt}'), 4326))
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
    load_kwargs: dict = {},
) -> float:
    """Calculate the area (m²) of the dataset within the given geometry."""

    if dataset_type == "stac-geoparquet":
        return _get_area_m2_from_stac_geoparquet(
            dataset_url,
            geometry_4326,
            indicator,
            value_list,
            datetime_string_match=datetime_string_match,
            load_kwargs=load_kwargs,
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
    geometry: Geometry,
    indicators: dict[str, dict],
    dataset_provenance_url: str,
    datetime_string_match: str | None = None,
    load_kwargs: dict = {},
) -> dict[str, str | float]:
    results = {}
    sd = sedona.db.connect()

    logger.info(f"Loading dataset from {dataset_provenance_url}")
    provenance = read_provenance(dataset_provenance_url)
    dataset_url = provenance.get("dataUrl")
    dataset_type = provenance.get("dataType")

    logger.info(f"Dataset URL: {dataset_url}")

    # Order sum area indicators first, then area percentages. Area percentages are dependent on area calculations.
    indicators = dict(
        sorted(indicators.items(), key=lambda item: ("percent-" in item[0], item[0]))
    )  # TODO: Make this more robust when there are non-area percent indicators.

    sum_area_var_pattern = re.compile(r"^sum-.*-area$")
    area_percent_var_pattern = re.compile(r"^percent-.*-area$")
    count_var_pattern = re.compile(r"^count-.*$")
    # TODO: Extend to other indicator types as needed by future products.

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
        geoms = [geometry]
        if geometry.geom_type == "MultiPolygon":
            geoms = list(geometry.geoms)

        # If any geom is over a certain size, tile it into smaller pieces. Without this, we cannot run Indonesia EEZ GMW v4 at full resolution for example.
        # For example this makes Australia's 8 geometries into 86.
        geoms_tiled = _tile_geometries(geoms)

        # These total_* indicators are the sums over all single geometries in the multipolygon
        # TODO: When doing the indicator refactor, generalise these.
        total_multipolygon_area_m2 = 0.0  # Need for percent area calculations
        total_indicator_area_m2 = 0.0
        total_count = 0
        for i, geom in enumerate(geoms_tiled):
            logger.info(f"Processing geom {i + 1} of {len(geoms_tiled)}")
            # For percent area calculations, we need the total area of the multipolygon in m²
            geometry_6933 = geom.to_crs("EPSG:6933")
            geom_area_m2 = geometry_6933.area
            total_multipolygon_area_m2 += geom_area_m2
            logger.info(f"Geom bbox: {geom.boundingbox}")
            # Area indicators
            if sum_area_var_pattern.match(
                var_key
            ):  # ["sum-mangrove-area", "sum-seagrass-area", "sum-reef-area", "sum-intertidal-area", "sum-saltmarsh-area"]
                area_m2 = _get_area_m2_from_dataset_geometry(
                    sd,
                    dataset_url,
                    dataset_type,
                    geom,
                    datetime_string_match=datetime_string_match,
                    indicator=indicator_name,
                    value_list=indicator_value_list,
                    load_kwargs=load_kwargs,
                )
                total_indicator_area_m2 += area_m2
                results[var_key] = total_indicator_area_m2
                logger.info(
                    f"Total area by value: {total_indicator_area_m2}m² for indicator {var_key}, value/s {indicator_value_s}"
                )
            elif count_var_pattern.match(var_key):  # ["count-buildings"]
                logger.info("Starting count indicator analysis...")
                # TODO: Try to parallelise this to improve performance on multipolygons with many parts, that each intersect many parquet files.
                count = _get_count_points_in_polygon_geoparquet(
                    sd, dataset_url, geom.wkt
                )
                total_count += count
                results[var_key] = total_count
                logger.info(
                    f"Total count of intersected buildings for this multipolygon geometry so far: {total_count}"
                )

        # Handle area percent indicators outside of the single geometry loop, since they depend on total area calculations
        if area_percent_var_pattern.match(var_key):
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
                f"Calculated {var_key}: {area_percent:.2f}% (Indicator area: {indicator_area_m2:.2f}m², Total geom area: {total_multipolygon_area_m2:.2f}m²)"
            )

    return results


def parse_outputs(df: pd.DataFrame) -> dict:
    outputs = {}

    for _, row in df.iterrows():
        timePoint = row["timePoint"]
        for indicator, value in row["indicators"].items():
            if indicator not in outputs:
                outputs[indicator] = {}
            output = {"geometryOutputId": row["geometryOutputId"], "value": value}

            if timePoint not in outputs[indicator]:
                outputs[indicator][timePoint] = []

            outputs[indicator][timePoint].append(output)

    return outputs
