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
    xarray_calculate_area,
)

logger = logging.getLogger()

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
        raise CSDRException(
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

    # TODO: Add S3 Authentication using Boto3CredentialProvider. Can pass aws.access_key_id and aws.secret_access_key to Sedona.
    sd.read_parquet(dataset_url, options={"aws.skip_signature": True, "aws.region": region}).to_view("dataset", overwrite=True)

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
    # Retry on failure. 2/160 geometries had a failure in Argo.
    # The failure is an invalid range request when reading the parquet file.
    # It is better to retry here for just one of potentially many parquet files, even though the workflow will retry the whole process_geometry.
    @retry(max_retries=3, retry_logger=logger)
    def count_points_parquet(row: pd.Series) -> int:
        country_code = row['country_code']
        s2_code = row['s2_code']
        partition_url = row['url']
        try:
            logging.info(f"Reading parquet: {partition_url}")
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
                logging.info(f"{geom_count} buildings for parquet {country_code}, {s2_code}")
                return int(geom_count)
            return 0
        except Exception:
            logging.exception(f"Error processing 2nd level country admin area parquet {country_code}/{s2_code}")
            raise

    for _idx, row in intersected_partition_urls.iterrows():
        try:
            total_count += count_points_parquet(row)
        except Exception:
            logging.exception(f"Failed to process 2nd level country admin area parquet {row['country_code']}/{row['s2_code']} after retries. Raising so workflow will retry.")
            raise
    
    logging.info(f"Total intersected buildings from all parquet files: {total_count}")
    return int(total_count)


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
        # This path config is specific to the the partitioned ACA reef geoparquet structure.
        path, _file_name = split_path_and_file_name_from_url(dataset_url)
        partition_path = f"{path}/partition/" # Needs trailing slash for Sedona to read all files in the partition folder
        return _get_area_from_geoparquet_sedona(sd, partition_path, geometry.wkt, variable, value, datetime_string_match=datetime_string_match)
    else:
        raise CSDRException(
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

    # Order area variables first, then area percentages. Area percentages are dependent on area calculations.
    variables = sorted(variables, key=lambda v: ("percent-" in v, v)) # TODO: Make this more robust when there are non-area percent variables.
    sum_area_var_pattern = re.compile(r"^sum-.*-area$")
    area_percent_var_pattern = re.compile(r"^percent-.*-area$")
    count_var_pattern = re.compile(r"^count-.*$")
    # TODO: Extend to other variable types as needed by future products.
    for var in variables:
        logging.info(f"Processing variable: {var}")
        # Explode multipolygon geometries to single polygons
        geoms = [geometry]
        if geometry.geom_type == "MultiPolygon":
            geoms = list(geometry.geoms)

        # TODO: When doing the variable refactor, generalise these.
        total_multipolygon_area = 0.0 # Need for percent area calculations
        total_variable_area = 0.0
        total_count = 0
        logging.info(f"Amount of single geometries: {len(geoms)}")
        for i, geom in enumerate(geoms):
            logging.info(f"Processing geom: {i} of {len(geoms)}")
            # Area variables
            if sum_area_var_pattern.match(var): # ["sum-mangrove-area", "sum-seagrass-area", "sum-reef-area", "sum-intertidal-area", "sum-saltmarsh-area"]
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
                total_variable_area += area
                results[var] = total_variable_area
                logging.info(f"Total area by value: {total_variable_area} for variable {var}")
            # Area percent variables
            elif area_percent_var_pattern.match(var): # ["percent-mangrove-area", "percent-intertidal-area", "percent-saltmarsh-area", "percent-seagrass-area"]
                logging.info("Starting percent variable analysis...")
                # The variables were sorted so all actual areas are already calculated before any hit this condition.
                # Here can we get the total area of the geometry first, then get the area by value, and calculate percent.
                # TODO: Reproject geometry to EPSG:6933 for area calculation consistency. Geoms are in EPSG:4326 in the DB (not sure about in the parquet file).
                # TODO: Check this for multipolygons - need total area of all geometries. Germany has 2 geometries.
                geom_area_m2 = geometry.area  # This is just this single geom's area
                total_multipolygon_area += geom_area_m2
                area_by_value = results.get(f"sum-{var.replace('percent-', '')}", 0.0)
                area_percent = (area_by_value / total_multipolygon_area * 100.0) if total_multipolygon_area > 0 else 0.0
                results[var] = area_percent
                logging.info(f"Calculated {var}: {area_percent:.2f}% (Area by value: {area_by_value:.2f} m^2, Total geom area: {total_multipolygon_area:.2f} m^2)")
            elif count_var_pattern.match(var): # ["count-buildings"]
                logging.info("Starting count variable analysis...")
                # TODO: Check this for multipolygons - need total count of all geometries?
                count = _get_count_points_in_polygon_geoparquet(
                    sd,
                    dataset_url,
                    geom.wkt
                )
                # TODO: Check this logic for summing. Germany has 2 geometries.
                total_count += count
                results[var] = total_count
                logging.info(f"Total count-buildings: {total_count}")
            else:
                logging.error(f"Unknown variable requested: {var}")
                raise CSDRException(f"Unknown variable requested: {var}")
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
