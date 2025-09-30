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


# Dataset GMW
cache-gmw-v4:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/ \
		--source-zip-name gmw_mng_2020_v4019_gtiff.zip \
		--target-location=cache/gmw/v4/

cache-gmw-v3:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/ \
		--source-zip-name gmw_v3_1996_gtiff.zip \
		--target-location=cache/gmw/v3/ \
		--years=all

cache-gmw-v3:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/ \
		--source-zip-name gmw_v3_{year}_gtiff.zip \
		--target-location=cache/gmw/v3/ \
		--years=1996,2007

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
