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
		--source-zip-location cache/eez/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location cache/eez/

geometry-eez-provenance:
	csdr provenance geometry \
		--id eez-v4 \
		--geometry-url=cache/eez/EEZ_land_union_v4_202410.parquet \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet

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
