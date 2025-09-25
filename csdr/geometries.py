from datetime import datetime

from geopandas import GeoDataFrame

from csdr.utils import make_uuid


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

    # Add csdr-id field
    timestamp = datetime.now().isoformat()
    gdf["csdr-id"] = [
        make_uuid(geometry_id + timestamp + str(i)) for i in range(len(gdf))
    ]

    return gdf
