import logging
from datetime import datetime

import pandas as pd
import sedona.db
from obstore.auth.boto3 import Boto3CredentialProvider
from odc.geo.geom import Geometry

from csdr.provenance import read_provenance
from csdr.utils import (
    check_for_any_intersection,
    load_xarray_stacgeoparquet,
    open_stacgeoparquet,
    xarray_calculate_area,
)


def _get_area_from_stac_geoparquet(dataset_url: str, geometry: Geometry, variable: str, value: float, datetime_string_match: str | None = None, load_kwargs: dict = {}) -> float:
    # Get the STAC items (just metadata, not the data itself, so dask chunking not needed yet)
    items = open_stacgeoparquet(dataset_url)
    logging.info(f"Dataset has {len(items)} STAC items.")

    # Performance optimisation to return quickly if no spatial intersection between geometry and dataset bounding boxes. For example landlocked geometries will not have any overlap with coastal/ocean datasets.
    # 1. Spatial intersect bounding boxes. STAC items have bounding boxes in metadata. Geometries are vector parquet, intersect with dataset STAC item bboxes.
    # 3. If no intersect, return 0.0 area immediately (fast!). Else do the actual calculation (because there is potential overlap).
    # STAC Geoparquet has proj:bbox attribute. STAC Geoparquet of Mangroves is sparse. There are 1647 STAC items, each with a bbox. Checking intersection of geometry bbox with these bboxes is very fast.
    # TODO: make this a param to use or not because if there were less sparse data it could slow processing down potentially?
    any_intersection = check_for_any_intersection(geometry, items)
    if not any_intersection:
        logging.info("No spatial intersection between bounding boxes of geometry and dataset. Returning area 0.0.")
        return 0.0
    else:
        logging.info("Spatial intersection found between bounding boxes of geometry and dataset. Proceeding with area calculation.")

    # Force the use of Dask. Important for loading the xarray. Without chunking, large datasets may not fit into memory. Chunked (lazy, parallel) loading is scaleable.
    if load_kwargs.get("chunks") is None:
        load_kwargs["chunks"] = {}
    logging.info(f"Loading dataset with chunking settings: {load_kwargs.get('chunks')}")
    
    # Load the dataset
    data = load_xarray_stacgeoparquet(
        items,
        geom=geometry,
        datetime_string_match=datetime_string_match,
        **load_kwargs,
    )

    logging.info(f"Loaded data with shape {data.dims}")

    if variable not in data.data_vars:
        raise ValueError(
            f"Variable {variable} not found in dataset. Available: {list(data.data_vars)}"
        )

    total_area = xarray_calculate_area(
        data[variable], geometry, variable=variable, value=value
    )

    return total_area


def _get_area_from_geoparquet_sedona(sd: sedona.db.SedonaContext, dataset_parquet_url: str, geometry_wkt: str, variable: str, value: float, datetime_string_match: str | None = None) -> float:
    # This should already handle the bbox intersection optimization internally
    # This does predicate pushdown and spatial filtering using Sedona rather than loading everything into memory
    # Local for development testing
    import pdb; pdb.set_trace()
    # url can be s3://, https://, or local.
    try:
        sd.read_parquet(
            # dataset_parquet_url
            # "s3://csdr-public-dev/datasets/aca/0-0-1/reefextent.parquet",
            # "https://csdr-public-dev.s3.ap-southeast-2.amazonaws.com/datasets/aca/0-0-1/reefextent.parquet",
            "https://files.auspatious.com/EEZ_land_union_v4_202410.parquet",
            # "./cache/datasets/aca/0-0-1/reefextent.parquet"
            # We can pass saws.access_key_id, aws.secret_access_key, aws.region, aws.endpoint, aws.skip_signature, aws.nosign, aws.bucket_name, aws.use_ssl, aws.force_path_style
            options={"aws.skip_signature": True, "aws.region": "ap-southeast-2"}, # TODO: is region a env var we can use instead of hardcoding?
        ).to_view("dataset2")

        # Calculate area using Sedona SQL (area in square meters after reprojecting to EPSG:6933)
        # TODO: Add filters for variable, value, and datetime_string_match
        # This part takes a super long time to run when the data is in S3. Super fast when local.
        # TODO: ST_Transform the geometry to 6933 before calculating area? Or does intersect handle that?
        area_result = sd.sql(f"""
        SELECT SUM(ST_Area(ST_Transform(geometry, 6933))) AS total_area
        FROM dataset2
        WHERE ST_Intersects(geometry, ST_SetSRID(ST_GeomFromText('{geometry_wkt}'), 4326))
        """).to_pandas()
        area_result = sd.sql(f"""
        SELECT * FROM dataset2 WHERE "UNION" = 'Algeria'
        """).to_memtable()
        val = area_result['total_area'][0]
        if pd.isna(val):
            return 0.0
        return round(float(val), 2)
    except Exception as e:
        logging.error(f"Error in Sedona processing: {e}", exc_info=True)
        raise


def _get_area_from_dataset_geometry(
    sd: sedona.db.SedonaContext,
    dataset_provenance_url: str,
    geometry: Geometry,
    variable: str,
    value: float,
    datetime_string_match: str | None = None,
    load_kwargs: dict = {},
) -> float:
    """Calculate the area of the dataset within the given geometry."""
    logging.info(f"Loading dataset from {dataset_provenance_url}")
    provenance = read_provenance(dataset_provenance_url)
    dataset_url = provenance.get("dataUrl")
    dataset_type = provenance.get("dataType")

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
    for var in variables:
        if var == "sum-area-by-value":
            # Explode multipolygon geometries to single polygons
            geoms = [geometry]
            if geometry.geom_type == "MultiPolygon":
                geoms = list(geometry.geoms)
            total_area = 0.0
            logging.info(f"Amount of single geometries: {len(geoms)}")
            start_time = datetime.now()
            for geom in geoms:
                area = _get_area_from_dataset_geometry(
                    sd,
                    dataset_provenance_url,
                    geom,
                    datetime_string_match=datetime_string_match,
                    variable=variable_name,
                    value=variable_value,
                    load_kwargs=load_kwargs,
                )
                total_area += area
            results["sum-area-by-value"] = total_area
            logging.info(f"Total area by value: {total_area}")
            total_seconds = round((datetime.now() - start_time).total_seconds(), 2)
            logging.info(f"Total time taken: {total_seconds} seconds")
        else:
            logging.error(f"Unknown variable requested: {var}")
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
