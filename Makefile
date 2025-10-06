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

geometry-eez-cache-s3:
	csdr eez cache \
		--target-location s3://files.auspatious.com/csdr/geometries/eez/0-0-1 \
		--overwrite

geometry-eez-convert:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location s3://files.auspatious.com/csdr/geometries/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location cache/eez/

geometry-eez-provenance:
	csdr provenance geometry \
		--id eez-v4 \
		--dataset-url=cache/eez/EEZ_land_union_v4_202410.parquet \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet

geometry-eez-provenance-db:
	csdr provenance geometry \
		--id c3592590-d42b-4e5c-8369-180fa7f1fcd7 \
		--dataset-url=cache/eez/EEZ_land_union_v4_202410.parquet \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--post-to-database \
		--post-geometry-outputs

# Product Seagrass EEZ
product-list-geometries:
	csdr products list-geometries \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/1-0-0/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=/tmp/test.json

product-seagrass-eez-fiji:
	csdr products process-geometry \
		--dataset-provenance-url=cache/seagrass/dep_s2_seagrass.parquet.provenance.json \
		--geometry-provenance-url=cache/eez/EEZ_land_union_v4_202410.parquet.provenance.json \
		--target-location=cache/products/seagrass_eez/ \
		--variable-name=seagrass \
		--variable-value=1 \
		--datetime-string-match="2024" \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=67f067c7-36d2-5c91-a3e2-30f4cb6be6e7

# Product GMW EEZ
# 148a1289-b2f7-54a2-9ec3-acff2ce24ace - test geom
product-gmw-eez-test-geom:
	csdr products process-geometry \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/1-0-0/EEZ_land_union_v4_202410.parquet.provenance.json \
		--target-location=cache/products/gmw_eez/ \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=148a1289-b2f7-54a2-9ec3-acff2ce24ace

# Dataset GMW
cache-gmw-v4:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmw-v4/raw/ \
		--source-zip-name gmw_mng_2020_v4019_gtiff.zip \
		--target-location=cache/gmw/v4/

cache-gmw-v3:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/ \
		--source-zip-name gmw_v3_1996_gtiff.zip \
		--target-location=cache/gmw/v3/ \

cache-gmw-v3-all-years:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/ \
		--source-zip-name gmw_v3_{year}_gtiff.zip \
		--target-location=cache/gmw/v3/ \
		--years=all

cache-gmw-v3-two-years:
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
