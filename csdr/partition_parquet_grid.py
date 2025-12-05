import geopandas as gpd
import pandas as pd
import numpy as np

# Load your global GeoParquet file
gdf = gpd.read_parquet("cache/datasets/aca/0-0-1/reefextent.parquet")

# Define grid edges
lon_edges = np.linspace(-180, 180, 11)  # 10 intervals
lat_edges = np.linspace(-90, 90, 11)    # 10 intervals

# Get centroid coordinates
gdf['lon'] = gdf.geometry.centroid.x
gdf['lat'] = gdf.geometry.centroid.y

# Assign grid cell indices
gdf['lon_bin'] = pd.cut(gdf['lon'], lon_edges, labels=False, include_lowest=True)
gdf['lat_bin'] = pd.cut(gdf['lat'], lat_edges, labels=False, include_lowest=True)

# Create a partition label
gdf['partition'] = gdf['lon_bin'].astype(str) + "_" + gdf['lat_bin'].astype(str)

# Write each partition to a separate Parquet file
for partition, group in gdf.groupby('partition'):
    group.drop(['lon', 'lat', 'lon_bin', 'lat_bin', 'partition'], axis=1).to_parquet(f"cache/datasets/aca/0-0-1/partition/reefextent_{partition}.parquet")