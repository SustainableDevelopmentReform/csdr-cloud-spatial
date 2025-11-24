# This makefile holds many test commands for working with datasets, geometries and products.
# It is the base for creating the workflows in the -flux repo.
# See here to get developing: ./how_to_run_dataset_eez-v4_workflow.md

### DATASETS ###

# Dataset GMW v4
cache-gmw-v4-local:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmw-v4/raw/gmw_mng_2020_v4019_gtiff.zip \
		--target-location=./cache/datasets/gmw-v4/raw \
		--overwrite

# Extracting takes a few minutes
# Extract target-location must be an absolute path! Otherwise STAC items will be made with broken href attributes.
extract-gmw-v4-local:
	csdr gmw extract \
		--source-location=./cache/datasets/gmw-v4/raw \
		--target-location=$(PWD)/cache/datasets/gmw-v4/0-0-1/data \
		--overwrite

# TODO: Check whether index source and target locations must be absolute paths too for STAC hrefs to be correct. Using absolute paths just in case.
index-gmw-v4-local:
	csdr gmw index \
		--source-location=$(PWD)/cache/datasets/gmw-v4/0-0-1/data \
		--target-location=$(PWD)/cache/datasets/gmw-v4/0-0-1 \
		--overwrite

# Make a Dataset in the app and use the ID here
provenance-gmw-v4-local-db:
	csdr provenance dataset \
		--id=97e943d9-4f37-4466-b0ef-162ed5e49368 \
		--dataset-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet \
		--source-url="https://example.com" \
		--source-metadata-url="https://example.com" \
		--dataset-type stac-geoparquet \
		--post-to-database \
		--overwrite

# Dataset GMW v3
cache-gmw-v3-single-file:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip \
		--target-location=./cache/datasets/gmw-v3/0-0-1

cache-gmw-v3-multiple-files:
	csdr gmw cache \
		--source-location=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2020_gtiff.zip \
		--target-location=./cache/datasets/gmw-v3/0-0-1

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




### GEOMETRIES ###

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
		--source-zip-location ./cache/geometries/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location ./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--create-pmtiles

geometry-eez-convert-s3:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--create-pmtiles

geometry-eez-convert-s3-public-dev:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location s3://csdr-public-dev/geometries/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--create-pmtiles

### EEZ provenance
geometry-eez-provenance-local:
	csdr provenance geometry \
		--id 6231cc07-5723-4c95-8e64-39322a9be2ed \
		--run-id=755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--dataset-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--overwrite

geometry-eez-provenance-local-db:
	csdr provenance geometry \
		--id 6231cc07-5723-4c95-8e64-39322a9be2ed \
		--run-id=755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--dataset-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--post-to-database \
		--post-geometry-outputs \
		--overwrite

geometry-eez-provenance-s3-db:
	csdr provenance geometry \
		--id <geometry_id> \
		--run-id=755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--dataset-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--dataset-type geoparquet \
		--post-to-database \
		--post-geometry-outputs \
		--overwrite






### PRODUCTS ###

# Product GMW EEZ V4.
# Steps:
# 1. List geometries - writes a list of geometries to a file from a geometry run provenance.
# 2. Process - processes the geometries for the product. I think this is a heavy step that can be killed by Out of Memory errors. Is it parallelized and distributed using Dask?
# 3. Consolidate - consolidates the processed geometries.
# 4. Provenance - generates provenance information for the product.

# How do we have the run id here? Maybe we make it and then pass it to the app when we make the product
# Product GMW EEZ V4 List Geometries
# This run ID is just for testing. It will actually to be passed from the workflow
product-gmw-v4-eez-list-geometries-local:
	csdr products list-geometries \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=./cache/tmp/geometries_list.json

product-gmw-v4-eez-list-geometries-s3:
	csdr products list-geometries \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=s3://csdr-public-dev/products/gmw-v4-eez/0-0-1/tmp/geometries_list.json

# Product GMW EEZ V4 Process Geometries
# variable-value=1.0 for mangrove presence. It is boolean raster with 1 for presence and 0 for absence.
# resolution=100 is 100m. 10 is max for GMW v4.
# https://epsg.io/6933
# Create a Product in the app. Use the product ID below. Select your dataset and geometry, and time as yearly.
product-gmw-v4-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--run-id=b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v4-eez/0-0-1/runs/b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024-01-01 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=605efc56-2be3-53ef-b5e4-c1c9127dcbae \
		--overwrite

product-gmw-v3-eez-process-geometry-s3:
	csdr products process-geometry \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--run-id=b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a \
		--geometry-provenance-url=s3://files.auspatious.com/csdr/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v3/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw_v3_eez/0-0-1/runs/b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime-string-match=1996 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=1643908b-6e6d-556f-ac60-226bed7d3b82 \
		--overwrite


# Process all geometries with Dask. The workflow does not use this but it is helpful for developing with Dask locally
product-gmw-v4-eez-process-all-geometries-dask-s3:
	csdr products process-all-geometries-dask \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--run-id=b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--target-location=s3://csdr-public-dev/products/gmw-v4-eez/0-0-1/runs/b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024-01-01 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--overwrite \
		--use-dask \
		--dask-opts="n_workers=2,threads_per_worker=2,memory_limit=8GB"

# TODO: make new run-id for this local test?
product-gmw-v4-eez-process-all-geometries-dask-local:
	csdr products process-all-geometries-dask \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--run-id=test_local_dask_run_id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v4-eez/0-0-1/runs/test_local_dask_run_id \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024-01-01 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--overwrite \
		--use-dask \
		--dask-opts="n_workers=2,threads_per_worker=2,memory_limit=8GB"

# Product GMW v4 EEZ Consolidate
product-gmw-v4-eez-consolidate-local:
	csdr products consolidate \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--run-id=test_local_dask_run_id \
		--location=./cache/products/gmw-v4-eez/0-0-1 \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--variable-name=mangrove \
		--datetime=2024-01-01

product-gmw-v4-eez-consolidate-s3:
	csdr products consolidate \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--run-id=b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a \
		--location s3://csdr-public-dev/products/testing/gmw-eez-100m \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--variable-name=mangrove \
		--datetime=2024-01-01

# You need to make a Product in the app before running provenance. Use that product ID here.
# You also need to make a Variable. It must have the ID 'sum-area-by-value'.
product-gmw-v4-eez-provenance-local-db:
	csdr provenance product \
		--product-id 935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--product-url=./cache/products/gmw-v4-eez/0-0-1/runs/b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a/mangrove/2024-01-01/935e9c13-7e2e-40c5-a4f8-f5f62ea54381.parquet \
		--run-id=b7e2e2b2-2e7a-4e7e-8e2a-7e2e2b2e7e2a \
		--dataset-run-id=dc364a0b-a719-4a39-b088-653dd28bb7a6 \
		--geometries-run-id=755206f2-dc2f-5b11-8355-2a86b34f7984 \
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




### OTHER ###

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
