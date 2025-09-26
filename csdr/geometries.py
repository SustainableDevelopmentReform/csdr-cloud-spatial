import json
from datetime import datetime
from json import dumps

from geopandas import GeoDataFrame
from loguru import logger
from odc.geo.geom import Geometry
from pandas import Series
from requests.exceptions import HTTPError

from csdr.app_integration import post_geometry_output, post_geometry_output_bulk
from csdr.io import read_geospatial_file
from csdr.utils import make_uuid


def convert_gdf_row_to_geometry_output(gdf_row: Series, crs: str) -> dict:
    poly = Geometry(gdf_row.geometry, crs=crs)
    properties = gdf_row.drop(labels=["geometry"]).to_dict()

    # Clean data, replace NaN with something that works in JSON
    for key, value in properties.items():
        if value != value:  # NaN check
            properties[key] = None

    assert poly.geom_type in ["Polygon", "MultiPolygon"], (
        f"Only Polygon and MultiPolygon geometries are supported, not {poly.geom_type}"
    )

    geometry_output = {
        # "id": properties.get("csdr-id"),  # Random generated ID
        "geometry": poly.geojson()["geometry"],
        "name": properties.get("csdr-name"),
        "description": properties.get("description", ""),
        "metadata": properties.get("metadata", {}),
        "properties": properties,
    }

    return geometry_output


def add_geometry_id_name(
    gdf: GeoDataFrame,
    name_field: str,
    geometry_id: str,
) -> GeoDataFrame:
    """
    Add 'csdr-id' and 'csdr-name' fields to a GeoDataFrame.

    Parameters:
    - gdf: GeoDataFrame to modify.
    - name_field: The field in the data to use for the 'Name' attribute.
    - geometry_id: Value to use for the id. Should be kebab-case, no spaces. If None, uses index-based ids.

    Returns:
    - Modified GeoDataFrame with 'csdr-id' and 'csdr-name' fields added.
    """
    if name_field not in gdf.columns:
        raise ValueError(
            f"Name field '{name_field}' not found in GeoDataFrame columns."
        )

    # Warn if name is not unique
    if gdf[name_field].duplicated().any():
        print(f"Warning: The name field '{name_field}' contains duplicate values.")

    # Add csdr-name field
    gdf["csdr-name"] = gdf[name_field]

    # Ensure there are no blank names
    if gdf["csdr-name"].isnull().any() or (gdf["csdr-name"] == "").any():
        raise ValueError("The 'csdr-name' field contains null or blank values.")

    # Add csdr-id field
    timestamp = datetime.now().isoformat()
    gdf["csdr-id"] = [
        make_uuid(geometry_id + timestamp + str(i)) for i in range(len(gdf))
    ]

    return gdf


def post_bulk_geometry_outputs_to_database(geometry_url: str, run_id: str) -> None:
    gpd = read_geospatial_file(geometry_url)

    bulk_output = {"geometriesRunId": run_id, "outputs": []}

    for _, row in gpd.iterrows():
        geometry_output = convert_gdf_row_to_geometry_output(row, gpd.crs)
        bulk_output["outputs"].append(geometry_output)

    response = post_geometry_output_bulk(bulk_output)

    try:
        response.raise_for_status()
    except HTTPError:
        logger.info(
            f"Failed to post geometry outputs: {json.dumps(bulk_output, indent=2)}"
        )
        logger.exception(
            f"Failed to post bulk geometry outputs to database. Response was \n{dumps(response.json(), indent=2)}"
        )

    logger.info(
        f"Wrote {len(bulk_output['outputs'])} bulk geometry outputs to database."
    )


def post_geometry_outputs_to_database(geometry_url: str, run_id: str) -> None:
    gpd = read_geospatial_file(geometry_url)
    errors = 0
    successes = 0
    for _, row in gpd.iterrows():
        geometry_output = convert_gdf_row_to_geometry_output(row, gpd.crs)
        geometry_output["geometriesRunId"] = run_id
        response = post_geometry_output(geometry_output)
        try:
            response.raise_for_status()
        except HTTPError:
            logger.error(
                f"Failed to post geometry output: {json.dumps(geometry_output, indent=2)}"
            )
            logger.exception(
                f"Failed to post geometry output to database. Response was \n{dumps(response.json(), indent=2)}"
            )
            errors += 1
        else:
            successes += 1
            logger.info(
                f"Wrote geometry output to database \n {dumps(response.json(), indent=2)}"
            )
    logger.info(
        f"Posted {successes} geometry outputs to database with {errors} errors."
    )
