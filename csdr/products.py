
import pandas as pd
from loguru import logger
from odc.geo.geom import Geometry
from pystac import ItemCollection

from csdr.provenance import read_provenance
from csdr.utils import (
    load_xarray_stacgeoparquet,
    open_stacgeoparquet,
    xarray_calculate_area,
)


def get_area_from_dataset_geometry(
    dataset_provenance_url: str,
    geometry: Geometry,
    variable: str,
    value: float,
    datetime_string_match: str | None = None,
    load_kwargs: dict = {},
) -> float:
    """Calculate the area of the dataset within the given geometry."""
    logger.info(f"Loading dataset from {dataset_provenance_url}")
    provenance = read_provenance(dataset_provenance_url)
    dataset_url = provenance.get("dataUrl")
    dataset_type = provenance.get("dataType")

    if dataset_type != "stac-geoparquet":
        raise ValueError(
            f"Unsupported dataset type: {dataset_type}. Only 'stac-geoparquet' is supported."
        )
    
    # Get the STAC items
    items = open_stacgeoparquet(dataset_url)

    # Force the use of Dask. Is this needed here? load_xarray_stacgeoparquet already does this. This is the only use of the load_xarray_stacgeoparquet function in the CLI (excluding a test).
    if load_kwargs.get("chunks") is None:
        load_kwargs["chunks"] = {}
    
    # Performance optimisation to return quickly if no spatial intersection between geometry and dataset bounding boxes. For example landlocked geometries will not have any overlap with coastal/ocean datasets.
    # 1. Spatial intersect bounding boxes. STAC items have bounding boxes in metadata. Geometries are vector parquet, intersect with dataset STAC item bboxes.
    # 3. If no intersect, return 0.0 area immediately (fast!). Else do the actual calculation (because there is potential overlap).
    # STAC Geoparquet has proj:bbox attribute. STAC Geoparquet of Mangroves is sparse. There are 1647 STAC items, each with a bbox. Checking intersection of geometry bbox with these bboxes is very fast.
    # TODO: move the check function outside of this function
    # TODO: make this a param to use or not because if there were less sparse data it could slow processing down potentially?
    def check_for_any_intersection(geometry: Geometry, stac_items: ItemCollection) -> bool:
        # make geometry bbox
        geom_bbox = geometry.boundingbox  # Should be [minx, miny, maxx, maxy]
        # Intersect geometry bbox with each STAC item bbox
        # If any intersect, return true
        # Else, return false
        for item in stac_items:
            item_bbox = item.properties.get("proj:bbox")
            if item_bbox is None:
                continue
            # Check for intersection. If none of these are true, the boxes must overlap in both x and y, so there is a spatial intersection.
            if not (
                geom_bbox[2] < item_bbox[0] # geom maxx is less than item minx
                or geom_bbox[0] > item_bbox[2] # geom minx is greater than item maxx
                or geom_bbox[3] < item_bbox[1] # geom maxy is less than item miny
                or geom_bbox[1] > item_bbox[3] # geom miny is greater than item maxy
            ):
                return True
        return False

    any_intersection = check_for_any_intersection(geometry, items)
    if not any_intersection:
        logger.info("No spatial intersection between geometry and dataset. Returning area 0.0.")
        return 0.0
    else:
        logger.info("Spatial intersection found between geometry and dataset bounding boxes. Proceeding with area calculation.")

    # Load the dataset
    data = load_xarray_stacgeoparquet(
        items,
        geom=geometry,
        datetime_string_match=datetime_string_match,
        **load_kwargs,
    )

    logger.info(f"Loaded data with shape {data.dims}")

    if variable not in data.data_vars:
        raise ValueError(
            f"Variable {variable} not found in dataset. Available: {list(data.data_vars)}"
        )

    total_area = xarray_calculate_area(
        data[variable], geometry, variable=variable, value=value
    )

    return total_area


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
    for var in variables:
        if var == "sum-area-by-value":
            geoms = [geometry]
            if geometry.geom_type == "MultiPolygon":
                geoms = list(geometry.geoms)
            total_area = 0.0
            logger.info(f"Amount of single geometries: {len(geoms)}")
            for geom in geoms:
                area = get_area_from_dataset_geometry(
                    dataset_provenance_url,
                    geom,
                    datetime_string_match=datetime_string_match,
                    variable=variable_name,
                    value=variable_value,
                    load_kwargs=load_kwargs,
                )
                total_area += area
            results["sum-area-by-value"] = total_area
            logger.info(f"Total area by value: {total_area}")
        else:
            logger.warning(f"Unknown variable requested: {var}")
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
