from loguru import logger
from odc.geo.geom import Geometry

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
    provenances = read_provenance(dataset_provenance_url)
    dataset_url = provenances.get("dataset_url")
    dataset_type = provenances.get("dataset_type")

    if dataset_type != "stac-geoparquet":
        raise ValueError(
            f"Unsupported dataset type: {dataset_type}. Only 'stac-geoparquet' is supported."
        )

    # Get the STAC items
    items = open_stacgeoparquet(dataset_url)

    if load_kwargs.get("chunks") is None:
        load_kwargs["chunks"] = {}

    # Load the dataset
    data = load_xarray_stacgeoparquet(
        items,
        geom=geometry,
        datetime_string_match=datetime_string_match,
        measurements=[variable],
        **load_kwargs,
    )
    logger.info(
        f"Loaded data with shape {data.dims} and variables {list(data.data_vars)}"
    )

    total_area = xarray_calculate_area(data, geometry, variable=variable, value=value)

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
            area_by_value = get_area_from_dataset_geometry(
                dataset_provenance_url,
                geometry,
                datetime_string_match=datetime_string_match,
                variable=variable_name,
                value=variable_value,
                load_kwargs=load_kwargs,
            )
            results["sum-area-by-value"] = area_by_value
            logger.info(f"Area by value: {area_by_value}")
        else:
            logger.warning(f"Unknown variable requested: {var}")
    return results
