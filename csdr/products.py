import logging
import re

import pandas as pd
import sedona.db
from odc.geo.geom import Geometry
from python_retry import retry

from csdr.io import split_path_and_file_name_from_url
from csdr.provenance import read_provenance
from csdr.utils import (
    CSDRException,
    load_xarray_stacgeoparquet,
    read_stacgeoparquet,
    xarray_calculate_area_m2,
)

logger = logging.getLogger()

# The _get_area_m2_from_stac_geoparquet function does the following:
# 1. Loads a STAC-Geoparquet using rustac.
# 2. If items found, loads the xarray dataset from the STAC items.
# 4. Calculates the area where the specified variable equals the given value within the geometry.
def _get_area_m2_from_stac_geoparquet(dataset_url: str, geometry: Geometry, variable: str, value: float, datetime_string_match: str | None = None, load_kwargs: dict = {}) -> float:
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
        raise CSDRException(
            f"Variable {variable} not found in dataset. Available: {list(data.data_vars)}"
        )

    # Calculate area (m²). This also does the variable/value filter.
    total_area_m2 = xarray_calculate_area_m2(
        data[variable], geometry, variable=variable, value=value
    )

    return total_area_m2


# TODO: Generalise this to work for any geoparquet dataset, not just ACA reef.
def _get_area_m2_from_geoparquet_sedona(
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

    # TODO: Add S3 Authentication using Boto3CredentialProvider. Can pass aws.access_key_id and aws.secret_access_key to Sedona.
    sd.read_parquet(dataset_url, options={"aws.skip_signature": True, "aws.region": region}).to_view("dataset", overwrite=True)

    area_result_m2 = sd.sql(
        f"""
        SELECT SUM(ST_Area(ST_Transform(geometry, 6933))) AS total_area_m2
        FROM dataset
        WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{geometry_wkt}'), 4326))
        """
    ).to_pandas()

    area_m2 = area_result_m2['total_area_m2'][0]
    if pd.isna(area_m2):
        logging.info("No intersected dataset geometries found.")
        return 0.0
    else:
        logging.info(f"Total intersected area: {area_m2:.2f}m²")

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
        logging.info("No intersected dataset geometries found in index.")
        return 0
    logging.info(f"Found {len(intersected_partition_urls)} intersected 2nd level country admin area parquet files from index.")

    total_count = 0
    # Retry on failure. This prevents the whole pod from rerunning, when just 1 or 2 of hundreds of requests fail because of Source Coop proxy.
    @retry(max_retries=3, retry_logger=logger)
    def count_points_parquet(row: pd.Series) -> int:
        country_code = row['country_code']
        s2_code = row['s2_code']
        partition_url = row['url']
        try:
            sd.read_parquet(partition_url).to_view("data", overwrite=True)
            count_result = sd.sql(
                f"""
                SELECT COUNT(*) AS geom_count
                FROM data
                WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{geometry_wkt}'), 4326))
                """
            ).to_pandas()
            geom_count = count_result['geom_count'][0]
            if not pd.isna(geom_count):
                logging.info(f"{geom_count} buildings for parquet.")
                return int(geom_count)
            return 0
        except Exception:
            logging.exception(f"Error processing 2nd level country admin area parquet {country_code}/{s2_code}")
            raise

    for idx, (_, row) in enumerate(intersected_partition_urls.iterrows(), 1):
        try:
            logging.info(f"Processing parquet {idx} of {len(intersected_partition_urls)}. Reading parquet: '{row.country_code}', '{row.s2_code}', '{row.url}'")
            total_count += count_points_parquet(row)
        except Exception:
            logging.exception(f"Failed to process 2nd level country admin area parquet {row.country_code}/{row.s2_code} after retries. Raising so workflow will retry.")
            raise
    
    return int(total_count)


def _get_area_m2_from_dataset_geometry(
    sd: sedona.db.context.SedonaContext,
    dataset_url: str,
    dataset_type: str,
    geometry: Geometry,
    variable: str,
    value: float,
    datetime_string_match: str | None = None,
    load_kwargs: dict = {},
) -> float:
    """Calculate the area (m²) of the dataset within the given geometry."""

    if dataset_type == "stac-geoparquet":
        return _get_area_m2_from_stac_geoparquet(dataset_url, geometry, variable, value, datetime_string_match=datetime_string_match, load_kwargs=load_kwargs)
    elif dataset_type == "geoparquet":
        # This path config is specific to the the partitioned ACA reef geoparquet structure.
        path, _file_name = split_path_and_file_name_from_url(dataset_url)
        partition_path = f"{path}/partition/" # Needs trailing slash for Sedona to read all files in the partition folder
        return _get_area_m2_from_geoparquet_sedona(sd, partition_path, geometry.wkt, variable, value, datetime_string_match=datetime_string_match)
    else:
        raise CSDRException(
            f"Unsupported dataset type: {dataset_type}. Only 'stac-geoparquet' and 'geoparquet' are supported."
        )


def process_variables_for_geometry(
    geometry: Geometry,
    variables: dict[str, dict],
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

    # Order sum area variables first, then area percentages. Area percentages are dependent on area calculations.
    variables = dict(sorted(variables.items(), key=lambda item: ("percent-" in item[0], item[0]))) # TODO: Make this more robust when there are non-area percent variables.

    sum_area_var_pattern = re.compile(r"^sum-.*-area$")
    area_percent_var_pattern = re.compile(r"^percent-.*-area$")
    count_var_pattern = re.compile(r"^count-.*$")
    # TODO: Extend to other variable types as needed by future products.
    for var_key, var_info in variables.items():
        variable_name = var_info.get("variable-name")
        variable_value = var_info.get("variable-value")
        # Try to convert variable_value to float if possible
        try:
            if variable_value is not None:
                variable_value = float(variable_value)
        except Exception:
            pass
        logging.info(f"Processing variable: {var_key} with variable name: {variable_name} and value: {variable_value}")
        # Explode multipolygon geometries to single polygons
        geoms = [geometry]
        if geometry.geom_type == "MultiPolygon":
            geoms = list(geometry.geoms)

        # These total_* variables are the sums over all single geometries in the multipolygon
        # TODO: When doing the variable refactor, generalise these.
        total_multipolygon_area_m2 = 0.0 # Need for percent area calculations
        total_variable_area_m2 = 0.0
        total_count = 0
        logging.info(f"Amount of single geometries: {len(geoms)}")
        for i, geom in enumerate(geoms):
            logging.info(f"Processing geom {i + 1} of {len(geoms)}")
            # For percent area calculations, we need the total area of the multipolygon in m²
            geometry_6933 = geom.to_crs("EPSG:6933")
            geom_area_m2 = geometry_6933.area
            total_multipolygon_area_m2 += geom_area_m2
            # Area variables
            if sum_area_var_pattern.match(var_key): # ["sum-mangrove-area", "sum-seagrass-area", "sum-reef-area", "sum-intertidal-area", "sum-saltmarsh-area"]
                area_m2 = _get_area_m2_from_dataset_geometry(
                    sd,
                    dataset_url,
                    dataset_type,
                    geom,
                    datetime_string_match=datetime_string_match,
                    variable=variable_name,
                    value=variable_value,
                    load_kwargs=load_kwargs,
                )
                total_variable_area_m2 += area_m2
                results[var_key] = total_variable_area_m2
                logging.info(f"Total area by value: {total_variable_area_m2}m² for variable {var_key}, value {variable_value}")
            elif count_var_pattern.match(var_key): # ["count-buildings"]
                logging.info("Starting count variable analysis...")
                # TODO: Try to parallelise this to improve performance on multipolygons with many parts, that each intersect many parquet files.
                count = _get_count_points_in_polygon_geoparquet(
                    sd,
                    dataset_url,
                    geom.wkt
                )
                total_count += count
                results[var_key] = total_count
                logging.info(f"Total count of intersected buildings for this multipolygon geometry so far: {total_count}")
            
        # Handle area percent variables outside of the single geometry loop, since they depend on total area calculations
        if area_percent_var_pattern.match(var_key):
            logging.info("Calculating percent area now that all geoms have been processed...")
            variable_area_m2 = results.get(f"sum-{var_key.replace('percent-', '')}", 0.0)
            area_percent = (variable_area_m2 / total_multipolygon_area_m2) * 100.0 if total_multipolygon_area_m2 > 0 else 0.0
            results[var_key] = area_percent
            logging.info(f"Calculated {var_key}: {area_percent:.2f}% (Variable area: {variable_area_m2:.2f}m², Total geom area: {total_multipolygon_area_m2:.2f}m²)")

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
