# This makefile holds a bunch of test commands for working with dataset, geometries and products

# Dataset Seagrass
dataset-seagrass-index:
	csdr seagrass index-dep

dataset-seagrass-provenance:
	csdr provenance dataset \
		--id dep-seagrass-regional-v1 \
		--dataset-url=cache/seagrass/dep_s2_seagrass.parquet \
		--source-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--source-metadata-url="https://example.com" \
		--dataset-type stac-geoparquet

# Geometry EEZ
geometry-eez-cache:
	csdr eez cache

geometry-eez-convert:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location s3://files.auspatious.com/csdr/geometries/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location cache/eez/

geometry-eez-provenance:
	csdr provenance geometry \
		--id eez-v4 \
		--geometry-url=cache/eez/EEZ_land_union_v4_202410.parquet \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet

geometry-eez-provenance-db:
	csdr provenance geometry \
		--id 65243c8f-355d-4b36-bd96-72de8c6f1bff \
		--dataset-url=cache/eez/EEZ_land_union_v4_202410.parquet \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--post-to-database \
		--post-geometry-outputs

# Product Seagrass EEZ
product-seagrass-eez-fiji:
	csdr products process-geometry \
		--dataset-provenance-url=cache/seagrass/dep_s2_seagrass.parquet.provenance.json \
		--geometry-provenance-url=cache/eez/EEZ_land_union_v4_202410.parquet.provenance.json \
		--target-location=cache/products/seagrass_eez/ \
		--variable-name=seagrass \
		--variable-value=1 \
		--datetime-string-match="2024" \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=84b8c461-5887-5593-b168-a127e7b25897

# Test GeoJSON
geometry-geojson-convert:
	csdr convert geo-to-parquet \
		--source-location tests/data/single_geometry.geojson \
		--target-location tests/data \
		--name-field=name \
		--overwrite

geometry-geojson-provenance:
	csdr provenance geometry \
		--dataset-url=tests/data/single_geometry.parquet \
		--dataset-type=geoparquet \
		--id=65243c8f-355d-4b36-bd96-72de8c6f1bff \
		--source-metadata-url=https://thing.com \
		--post-to-database \
		--post-geometry-outputs \
		--no-post-geometry-in-bulk
