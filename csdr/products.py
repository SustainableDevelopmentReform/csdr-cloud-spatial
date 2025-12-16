import logging
from datetime import datetime

import pandas as pd
import sedona.db
from odc.geo.geom import Geometry

from csdr.io import split_path_and_file_name_from_url
from csdr.provenance import read_provenance
from csdr.utils import (
    load_xarray_stacgeoparquet,
    read_stacgeoparquet,
    xarray_calculate_area,
)


# The _get_area_from_stac_geoparquet function does the following:
# 1. Loads a STAC-Geoparquet using rustac.
# 2. If items found, loads the xarray dataset from the STAC items.
# 4. Calculates the area where the specified variable equals the given value within the geometry.
def _get_area_from_stac_geoparquet(dataset_url: str, geometry: Geometry, variable: str, value: float, datetime_string_match: str | None = None, load_kwargs: dict = {}) -> float:
    """ Calculate the area of the dataset within the given geometry. """
    # Get the STAC items filtered by geometry and datetime
    items = read_stacgeoparquet(dataset_url)

    if not items or len(items) == 0:
        raise ValueError("No STAC items found.")

    # Force the use of Dask. Important for loading the xarray. Without chunking, large datasets may not fit into memory. Chunked (lazy, parallel) loading is scaleable.
    if load_kwargs.get("chunks") is None:
        load_kwargs["chunks"] = {}
    logging.info(f"Loading dataset with chunking settings: {load_kwargs.get('chunks')}")
    
    # Load the dataset as xarray from the STAC items. Filter spatially and temporally.
    data = load_xarray_stacgeoparquet(
        items,
        geometry=geometry,
        datetime_string_match=datetime_string_match,
        **load_kwargs,
    )

    logging.info(f"Loaded data with shape {data.dims}")

    if variable not in data.data_vars:
        raise ValueError(
            f"Variable {variable} not found in dataset. Available: {list(data.data_vars)}"
        )

    # Calculate area. This also does the variable/value filter.
    total_area = xarray_calculate_area(
        data[variable], geometry, variable=variable, value=value
    )

    return total_area


# TODO: Generalise this to work for any geoparquet dataset, not just ACA reef.
def _get_area_from_geoparquet_sedona(
        sd: sedona.db.context.SedonaContext,
        dataset_url: str,
        geometry_wkt: str,
        variable: str | None = None,
        value: float | None = None,
        datetime_string_match: str | None = None
    ) -> float:
    # This should already handle the bbox intersection optimization internally
    # This does predicate pushdown and spatial filtering using Sedona rather than loading everything into memory
    # Local for development testing
    # url can be s3://, https://, or local.

    # TODO: Add filters for variable, value, and datetime_string_match

    # TODO: Add S3 Authentication using Boto3CredentialProvider. Can pass aws.access_key_id and aws.secret_access_key to Sedona.
    region = "ap-southeast-2" # TODO: Get this from env/config.

    start_time = datetime.now()

    # This is for S3 data like the ACA reef dataset.
    # This is the reef stuff that needs to be generalised.
    path, _file_name = split_path_and_file_name_from_url(dataset_url)
    partition_path = f"{path}/partition/" # Needs trailing slash for Sedona to read all files in the partition folder
    sd.read_parquet(partition_path, options={"aws.skip_signature": True, "aws.region": region}).to_view("dataset", overwrite=True)
    # TODO: Remove timing logs later
    total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
    logging.info(f"Time taken to initialise: {total_seconds} seconds")

    start_time = datetime.now()
    area_result = sd.sql(
        f"""
        SELECT SUM(ST_Area(ST_Transform(geometry, 6933))) AS total_area
        FROM dataset
        WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{geometry_wkt}'), 4326))
        """
    ).to_pandas()

    area_m2 = area_result['total_area'][0]
    if pd.isna(area_m2):
        logging.info("No intersected dataset geometries found.")
        return 0.0
    else:
        logging.info(f"Total intersected area: {area_m2:.2f} m^2")

    # TODO: Remove timing logs later
    total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
    logging.info(f"Time taken to calculate: {total_seconds} seconds")
    return round(float(area_m2), 2)

def _get_count_points_in_polygon_geoparquet(
        sd: sedona.db.context.SedonaContext,
        dataset_url: str,
        geometry_wkt: str,
    ) -> int:
    # buildings.parquet is in EPSG:4326.

    # This is for Source.coop data - VIDA Buildings dataset.
    # Buildings steps:
    # 1. Use sedona to intersect with buildings.parquet index file that we make.
    # 2. Then know which country parquets to load based on that intersection. Use sedona again to load only those parquets.
    # 3. Calculate count of buildings from those parquets.

    # EPSG:4326
    sd.read_parquet(dataset_url).to_view("index_data", overwrite=True)

    # TODO: Just use the geometry column directly rather than reconstructing from minx, miny, maxx, maxy.
    intersected_partition_urls = sd.sql(
        f"""
        WITH indexed AS (
            SELECT
                code,
                url,
                ST_SetSRID(
                    ST_GeomFromText(
                        'POLYGON((' ||
                            minx || ' ' || miny || ', ' ||
                            maxx || ' ' || miny || ', ' ||
                            maxx || ' ' || maxy || ', ' ||
                            minx || ' ' || maxy || ', ' ||
                            minx || ' ' || miny ||
                        '))'
                    ),
                    4326
                ) AS bbox_polygon
            FROM (
                SELECT
                    code,
                    url,
                    CAST(trim(string_to_list(regexp_replace(bbox, '\\[|\\]', '', 'g'), ',')[1]) AS DOUBLE) AS minx,
                    CAST(trim(string_to_list(regexp_replace(bbox, '\\[|\\]', '', 'g'), ',')[2]) AS DOUBLE) AS miny,
                    CAST(trim(string_to_list(regexp_replace(bbox, '\\[|\\]', '', 'g'), ',')[3]) AS DOUBLE) AS maxx,
                    CAST(trim(string_to_list(regexp_replace(bbox, '\\[|\\]', '', 'g'), ',')[4]) AS DOUBLE) AS maxy
                FROM index_data
            )
        )
        SELECT
            code,
            url,
            bbox_polygon
        FROM indexed
        WHERE ST_Intersects(
            bbox_polygon,
            ST_SetSRID(ST_GeomFromText('{geometry_wkt}'), 4326)
        );
        """
    ).to_pandas()

    if intersected_partition_urls.empty:
        logging.info("No intersected dataset geometries found in index.")
        return 0
    logging.info(f"Found {len(intersected_partition_urls)} intersected country parquet files from index.")

    # Just for debugging
    intersected_partition_urls = intersected_partition_urls.head(2)
    total_count = 0
    for code, partition_url in intersected_partition_urls['url']:
        logging.info(f"Reading country parquet: {partition_url}")
        start_time = datetime.now()
        sd.read_parquet(partition_url).to_view("country_data", overwrite=True)
        # TODO: Remove timing logs later
        total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
        logging.info(f"Time taken to init: {total_seconds} seconds")
        start_time = datetime.now()
        country_count_result = sd.sql(
            f"""
            SELECT COUNT(*) AS country_geom_count
            FROM country_data
            WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{geometry_wkt}'), 4326))
            """
        ).to_pandas()
        country_geom_count = country_count_result['country_geom_count'][0]
        # TODO: Remove timing logs later
        total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
        logging.info(f"Time taken to calculate: {total_seconds} seconds")
        if not pd.isna(country_geom_count):
            total_count += country_geom_count
            logging.info(f"{country_geom_count} buildings for country parquet {code}")
    
    logging.info(f"Total intersected buildings from all countries: {total_count}")
    return total_count


def _get_area_from_dataset_geometry(
    sd: sedona.db.context.SedonaContext,
    dataset_url: str,
    dataset_type: str,
    geometry: Geometry,
    variable: str,
    value: float,
    datetime_string_match: str | None = None,
    load_kwargs: dict = {},
) -> float:
    """Calculate the area of the dataset within the given geometry."""

    if dataset_type == "stac-geoparquet":
        return _get_area_from_stac_geoparquet(dataset_url, geometry, variable, value, datetime_string_match=datetime_string_match, load_kwargs=load_kwargs)
    elif dataset_type == "geoparquet":
        return _get_area_from_geoparquet_sedona(sd, dataset_url, geometry.wkt, variable, value, datetime_string_match=datetime_string_match)
    else:
        raise ValueError(
            f"Unsupported dataset type: {dataset_type}. Only 'stac-geoparquet' and 'geoparquet' are supported."
        )


def process_variables_for_geometry(
    geometry: Geometry,
    variables: list[str],
    dataset_provenance_url: str,
    datetime_string_match: str | None = None,
    variable_name: str = "asset",
    variable_value: float | int | None = None,
    load_kwargs: dict = {},
) -> dict[str, str | float]:
    results = {}
    sd = sedona.db.connect()


    logging.info(f"Loading dataset from {dataset_provenance_url}")
    provenance = read_provenance(dataset_provenance_url)
    dataset_url = provenance.get("dataUrl")
    dataset_type = provenance.get("dataType")

    for var in variables:
        # Explode multipolygon geometries to single polygons
        geoms = [geometry]
        if geometry.geom_type == "MultiPolygon":
            geoms = list(geometry.geoms)
        total_area = 0.0
        logging.info(f"Amount of single geometries: {len(geoms)}")
        for geom in geoms:
            if var == "sum-area-by-value":
                area = _get_area_from_dataset_geometry(
                    sd,
                    dataset_url,
                    dataset_type,
                    geom,
                    datetime_string_match=datetime_string_match,
                    variable=variable_name,
                    value=variable_value,
                    load_kwargs=load_kwargs,
                )
                total_area += area
                results["sum-area-by-value"] = total_area
                logging.info(f"Total area by value: {total_area}")
            elif var == "count-buildings":
                count = _get_count_points_in_polygon_geoparquet(
                    sd,
                    dataset_url,
                    geom.wkt
                )
                results["count-buildings"] = count
                logging.info(f"Total count-buildings: {count}")
            else:
                logging.error(f"Unknown variable requested: {var}")
                raise ValueError(f"Unknown variable requested: {var}")
    return results


def parse_outputs(df: pd.DataFrame) -> dict:
    outputs = {}

    for _, row in df.iterrows():
        timePoint = row["timePoint"]
        for variable, value in row["variables"].items():
            if variable not in outputs:
                outputs[variable] = {}
            output = {"geometryOutputId": row["geometryOutputId"], "value": value}

            if timePoint not in outputs[variable]:
                outputs[variable][timePoint] = []

            outputs[variable][timePoint].append(output)

    return outputs
