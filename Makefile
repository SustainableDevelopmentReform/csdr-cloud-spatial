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
		--target-location s3://files.auspatious.com/csdr/geometries/eez/1-0-0 \
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
		--pmtiles-url=cache/eez/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--post-to-database \
		--post-geometry-outputs


geometry-eez-convert-s3:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location s3://files.auspatious.com/csdr/geometries/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location s3://files.auspatious.com/csdr/geometries/

geometry-eez-provenance-s3-db:
	csdr provenance geometry \
		--id c3592590-d42b-4e5c-8369-180fa7f1fcd7 \
		--dataset-url=s3://files.auspatious.com/csdr/geometries/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=s3://files.auspatious.com/csdr/geometries/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--post-to-database \
		--post-geometry-outputs

# Product Seagrass EEZ
product-list-geometries:
	csdr products list-geometries \
		--geometry-provenance-url=cache/eez/EEZ_land_union_v4_202410.parquet.provenance.json \
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
# 4cb61e58-9575-5d6b-ae0e-bae108b68634 - oom killed
# 3fca613b-7749-5e11-b371-3a977fb57804 - oom killed
product-gmw-v4-eez-test-geom:
	csdr products process-geometry \
		--product-id=temp-id-please-ignore \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--geometry-provenance-url=s3://files.auspatious.com/csdr/geometries/EEZ_land_union_v4_202410.parquet.provenance.json \
		--target-location=cache/products/gmw_v4_eez/ \
		--version=0.0.2 \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024-01-01 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=1643908b-6e6d-556f-ac60-226bed7d3b82 \
		--overwrite

product-gmw-v3-eez-test-geom:
	csdr products process-geometry \
		--product-id=temp-id-please-ignore \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v3/0-0-1/gmw.parquet.provenance.json \
		--geometry-provenance-url=s3://files.auspatious.com/csdr/geometries/EEZ_land_union_v4_202410.parquet.provenance.json \
		--target-location=cache/products/gmw_v3_eez/ \
		--version=0.0.2 \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime-string-match=2000 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=1643908b-6e6d-556f-ac60-226bed7d3b82 \
		--overwrite


product-gmw-eez-all-geom:
	csdr products process-all-geometries \
		--product-id=temp-id-please-ignore \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--geometry-provenance-url=s3://files.auspatious.com/csdr/geometries/EEZ_land_union_v4_202410.parquet.provenance.json \
		--target-location=cache/products/gmw_eez/ \
		--version=0.0.2 \
		--variable-name=mangrove \
		--variable-value=1 \
		--datetime=2024 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--overwrite

product-gmw-eez-consolidate:
	csdr products consolidate \
		--product-id=temp-id-please-ignore \
		--version=0.0.2 \
		--location=cache/products/gmw_eez/ \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--geometry-provenance-url=s3://files.auspatious.com/csdr/geometries/EEZ_land_union_v4_202410.parquet.provenance.json \
		--variable-name=mangrove

product-gmw-eez-provenance-db:
	csdr provenance product \
		--product-id de0e7d09-d238-470f-ad12-112fe70f1c2a \
		--product-url=cache/products/gmw_eez/temp-id-please-ignore/mangrove/0-0-2/temp-id-please-ignore-0-0-2.parquet \
		--dataset-run-id=1ad46e15-9999-49da-b02a-e44d47140a31 \
		--geometries-run-id=9ea66fba-1bcb-4bff-944f-2c29c5de1d78 \
		--post-to-database \
		--overwrite

product-gmw-eez-consolidate-s3:
	csdr products consolidate \
		--product-id=f7cf7d28-9e39-4e3c-8102-705fc3eb40a0 \
		--version=0.0.1 \
		--location s3://csdr-public-dev/products/testing/gmw-eez-100m \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/EEZ_land_union_v4_202410.parquet.provenance.json \
		--variable-name=mangrove

# Dataset GMW v4
cache-gmw-v4:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmw-v4/raw/gmw_mng_2020_v4019_gtiff.zip \
		--target-location=cache/gmw/v4/raw

extract-gmw-v4:
	csdr gmw extract \
		--source-location=cache/gmw/v4/raw \
		--target-location=cache/gmw/v4/data

index-gmw-v4:
	csdr gmw index \
		--source-location=cache/gmw/v4/data \
		--target-location=cache/gmw/v4

provenance-gmw-v4:
	csdr provenance dataset \
		--id gmw-v4 \
		--dataset-url=cache/gmw/v4/gmw.parquet \
		--source-url="https://example.com" \
		--source-metadata-url="https://example.com" \
		--dataset-type stac-geoparquet


# Dataset GMW v3
cache-gmw-v3-single-file:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip \
		--target-location=cache/gmw/v3/

cache-gmw-v3-multiple-files:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2020_gtiff.zip \
		--target-location=cache/gmw/v3/

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
