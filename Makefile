# This makefile holds a bunch of test commands for working with dataset, geometries and products

# Dataset Seagrass
dataset-seagrass-index:
	csdr seagrass index-dep

dataset-seagrass-provenance:
	csdr provenance dataset \
		--id dep-seagrass-regional-v1 \
		--dataset-url=./cache/seagrass/dep_s2_seagrass.parquet \
		--source-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--source-metadata-url="https://example.com" \
		--dataset-type stac-geoparquet

# Geometry EEZ
### EEZ cache
geometry-eez-cache-local:
	csdr eez cache \
		--target-location ./cache/eez-v4/0-0-1/raw \
		--overwrite

geometry-eez-cache-s3:
	csdr eez cache \
		--target-location s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/raw \
		--overwrite

geometry-eez-cache-s3-public-dev:
	csdr eez cache \
		--target-location s3://csdr-public-dev/geometries/eez-v4/0-0-1/raw \
		--overwrite
		
### EEZ convert
geometry-eez-convert-local:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location ./cache/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location ./cache/eez-v4/0-0-1/runs/test-run-id \
		--create-pmtiles

geometry-eez-convert-s3:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/runs/test-run-id \
		--create-pmtiles

geometry-eez-convert-s3-public-dev:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location s3://csdr-public-dev/geometries/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id \
		--create-pmtiles

### EEZ provenance
geometry-eez-provenance-local:
	csdr provenance geometry \
		--id <geometry_id> \
		--run-id=test-run-id \
		--dataset-url=./cache/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=./cache/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--overwrite

# json is local. how to write to local db too? configure host setting when running container and run local app server
geometry-eez-provenance-local-db:
	csdr provenance geometry \
		--id <geometry_id> \
		--run-id=test-run-id \
		--dataset-url=./cache/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=./cache/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--post-to-database \
		--post-geometry-outputs \
		--overwrite

geometry-eez-provenance-s3-db:
	csdr provenance geometry \
		--id <geometry_id> \
		--run-id=test-run-id \
		--dataset-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--post-to-database \
		--post-geometry-outputs \
		--overwrite

# Products - First step for all products is to list geometries from a geometry run provenance

# Product GMW EEZ V4.
# Steps:
# 1. List geometries - writes a list of geometries to a file from a geometry run provenance.
# 2. Process - processes the geometries for the product. I think this is a heavy step that can be killed by Out of Memory errors. Is it parallelized and distributed using Dask?
# 3. Consolidate - consolidates the processed geometries.
# 4. Provenance - generates provenance information for the product.

# Product GMW EEZ V4 List Geometries
product-gmw-v4-eez-list-geometries-local:
	csdr products list-geometries \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/f574ad55-1a73-5087-8317-4fda4d32ade2/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=./cache/products/gmw-v4-eez/0-0-1/runs/test-run-id/geometries_list.json

product-gmw-v4-eez-list-geometries-s3:
	csdr products list-geometries \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=s3://csdr-public-dev/products/gmw-v4-eez/0-0-1/runs/test-run-id/geometries_list.json

# Product GMW EEZ V4 Process Geometries
# 2 EEZs killed by OOM. Need to figure out if this problem persists.
# variable-value=1.0 for mangrove presence. It is boolean raster with 1 for presence and 0 for absence.
# resolution=100 is 100m. 10 is max for GMW v4.
# https://epsg.io/6933
# This is not using Dask (the default). Should it?
# geometry-id is Burundi's EEZ. 691f98c9-f9da-5987-b994-023afefc6563 # This is a good test to exit quickly because there won't be mangroves there.
# geometry-id is Fiji's EEZ. 50a3e198-6cc8-54c8-b2af-0585b8efbdd1 # This is a good test for mangroves.
product-gmw-v4-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=test-product-id \
		--run-id=test-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/f574ad55-1a73-5087-8317-4fda4d32ade2/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v4-eez/0-0-1 \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024-01-01 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=753afd2b-dabd-5286-9e82-79fa519f2578 \
		--overwrite

product-gmw-v3-eez-process-geometry-s3:
	csdr products process-geometry \
		--product-id=test-product-id \
		--run-id=test-run-id \
		--geometry-provenance-url=s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v3/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw_v3_eez/0-0-1 \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime-string-match=1996 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=1643908b-6e6d-556f-ac60-226bed7d3b82 \
		--overwrite

# Process all geometries
product-gmw-v4-eez-process-all-geometries-local:
	csdr products process-all-geometries \
		--product-id=test-product-id \
		--run-id=test-run-id \
		--geometry-provenance-url=s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v3/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw_v3_eez/0-0-1 \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--overwrite

product-gmw-v4-eez-process-all-geometries-s3:
	csdr products process-all-geometries \
		--product-id=test-product-id \
		--run-id=test-run-id \
		--geometry-provenance-url=s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v4-eez/0-0-1 \
		--variable-name=mangrove \
		--variable-value=1 \
		--datetime=2024 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--overwrite

# Product GMW v4 EEZ Consolidate
product-gmw-v4-eez-consolidate-local:
	csdr products consolidate \
		--product-id=test-product-id \
		--run-id=test-run-id \
		--location=./cache/products/gmw-v4-eez/0-0-1 \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--variable-name=mangrove \
		--datetime=2024-01-01

product-gmw-v4-eez-consolidate-s3:
	csdr products consolidate \
		--product-id=test-product-id \
		--run-id=test-run-id \
		--location s3://csdr-public-dev/products/testing/gmw-eez-100m \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--variable-name=mangrove \
		--datetime=2024-01-01

# Product GMW v4 EEZ Provenance
product-gmw-v4-eez-provenance-db-local:
	csdr provenance product \
		--product-id de0e7d09-d238-470f-ad12-112fe70f1c2a \
		--run-id=test-run-id \
		--product-url=./cache/products/gmw-v4-eez/0-0-1/runs/test-run-id/mangrove/2024-01-01/test-product-id.parquet
		--dataset-run-id=1ad46e15-9999-49da-b02a-e44d47140a31 \
		--geometries-run-id=9ea66fba-1bcb-4bff-944f-2c29c5de1d78 \
		--post-to-database \
		--overwrite

# Product Seagrass EEZ v4
product-seagrass-eez-fiji:
	csdr products process-geometry \
		# --product-id=<product_id> \
		--geometry-provenance-url=./cache/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/seagrass/dep_s2_seagrass.parquet.provenance.json \
		--target-location=./cache/products/seagrass_eez/ \
		--variable-name=seagrass \
		--variable-value=1 \
		--datetime-string-match="2024" \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=67f067c7-36d2-5c91-a3e2-30f4cb6be6e7

# Dataset GMW v4
cache-gmw-v4:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmw-v4/raw/gmw_mng_2020_v4019_gtiff.zip \
		--target-location=./cache/gmw/v4/raw

extract-gmw-v4:
	csdr gmw extract \
		--source-location=./cache/gmw/v4/raw \
		--target-location=./cache/gmw/v4/data

index-gmw-v4:
	csdr gmw index \
		--source-location=./cache/gmw/v4/data \
		--target-location=./cache/gmw/v4

provenance-gmw-v4:
	csdr provenance dataset \
		--id gmw-v4 \
		--dataset-url=./cache/gmw/v4/gmw.parquet \
		--source-url="https://example.com" \
		--source-metadata-url="https://example.com" \
		--dataset-type stac-geoparquet

# Dataset GMW v3
cache-gmw-v3-single-file:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip \
		--target-location=./cache/gmw/v3/

cache-gmw-v3-multiple-files:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2020_gtiff.zip \
		--target-location=./cache/gmw/v3/

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
