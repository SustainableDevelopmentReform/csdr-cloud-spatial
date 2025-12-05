# This makefile holds many test commands for working with datasets, geometries and products.
# It is the base for creating the workflows in the -flux repo.
# See here to get developing: ./how_to_run_dataset_eez-v4_workflow.md

### DATASETS ###

# Dataset GMW v4
cache-gmw-v4-local:
	csdr gmw cache \
		--source-locations=https://files.auspatious.com/gmw-v4/raw/gmw_mng_2020_v4019_gtiff.zip \
		--target-location=./cache/datasets/gmw-v4/0-0-1/raw \
		--out-file=/tmp/cached_files.json \
		--overwrite

cache-gmw-v4-s3:
	csdr gmw cache \
		--source-locations=https://files.auspatious.com/gmw-v4/raw/gmw_mng_2020_v4019_gtiff.zip \
		--target-location=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/raw \
		--out-file=/tmp/cached_files.json \
		--overwrite

# Extracting takes a few minutes
# Extract target-location must be an absolute path (for local store)! Otherwise STAC items will be made with broken href attributes.
extract-gmw-v4-local:
	csdr gmw extract \
		--source-location=./cache/datasets/gmw-v4/0-0-1/raw \
		--source-zip-name=gmw_mng_2020_v4019_gtiff.zip \
		--target-location=$(PWD)/cache/datasets/gmw-v4/0-0-1/data \
		--overwrite

extract-gmw-v4-s3:
	csdr gmw extract \
		--source-location=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/raw \
		--source-zip-name=gmw_mng_2020_v4019_gtiff.zip \
		--target-location=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/data \
		--overwrite

# TODO: Check whether index source and target locations must be absolute paths (for local store) too for STAC hrefs to be correct. Using absolute paths just in case.
index-gmw-v4-local:
	csdr gmw index \
		--source-location=$(PWD)/cache/datasets/gmw-v4/0-0-1/data \
		--target-location=$(PWD)/cache/datasets/gmw-v4/0-0-1 \
		--overwrite

index-gmw-v4-s3:
	csdr gmw index \
		--source-location=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/data \
		--target-location=s3://csdr-public-dev/datasets/gmw-v4/0-0-1 \
		--overwrite

# Make a Dataset in the app and use the ID here
provenance-gmw-v4-local-db:
	csdr provenance dataset \
		--id=5714917f-3549-4a95-9fc4-ff96efbdf311 \
		--dataset-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet \
		--source-url="https://zenodo.org/records/12756047" \
		--source-metadata-url="https://zenodo.org/records/12756047" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite

provenance-gmw-v4-s3-db:
	csdr provenance dataset \
		--id=5714917f-3549-4a95-9fc4-ff96efbdf311 \
		--dataset-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet \
		--source-url="https://zenodo.org/records/12756047" \
		--source-metadata-url="https://zenodo.org/records/12756047" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite


# Dataset GMW v3
# https://zenodo.org/records/6894273/files/gmw_v3_1996_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2007_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2008_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2009_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2010_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2015_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2016_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2017_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2018_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2019_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2020_gtiff.zip
# One file/year
cache-gmw-v3-local-single-file:
	csdr gmw cache \
		--source-locations=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip \
		--target-location=./cache/datasets/gmw-v3/0-0-1/raw \
		--out-file=/tmp/cached_files.json \
		--overwrite
# Many files/years
# Not sure if we should use the Zenodo or Auspatious links.
# https://zenodo.org/records/6894273/files/gmw_v3_1996_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2007_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2008_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2009_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2010_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2015_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2016_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2017_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2018_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2019_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2020_gtiff.zip
# https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2007_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2008_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2009_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2010_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2015_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2016_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2017_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2018_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2019_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2020_gtiff.zip
cache-gmw-v3-local-multiple-files:
	csdr gmw cache \
		--source-locations=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2007_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2008_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2009_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2010_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2015_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2016_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2017_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2018_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2019_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2020_gtiff.zip \
		--target-location=./cache/datasets/gmw-v3/0-0-1/raw \
		--out-file=/tmp/cached_files.json \
		--no-overwrite

cache-gmw-v3-s3-multiple-files:
	csdr gmw cache \
		--source-locations=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2007_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2008_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2009_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2010_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2015_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2016_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2017_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2018_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2019_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2020_gtiff.zip \
		--target-location=s3://csdr-public-dev/datasets/gmw-v3/0-0-1/raw \
		--out-file=/tmp/cached_files.json \
		--no-overwrite

# Example output of cache: ["./cache/datasets/gmw-v3/raw/gmw_v3_1996_gtiff.zip","./cache/datasets/gmw-v3/raw/gmw_v3_2020_gtiff.zip"]
# Example output of list-file-names: ["gmw_v3_1996_gtiff.zip", "gmw_v3_2020_gtiff.zip"]

## Extract v3
# Local paths must be absolute for STAC hrefs to be correct!!
extract-gmw-v3-local:
	csdr gmw extract \
		--source-location=./cache/datasets/gmw-v3/0-0-1/raw \
		--source-zip-name=gmw_v3_1996_gtiff.zip \
		--target-location=$(PWD)/cache/datasets/gmw-v3/0-0-1/data \
		--overwrite
# 		--source-zip-name=gmw_v3_2020_gtiff.zip \

extract-gmw-v3-s3:
	csdr gmw extract \
		--source-location=s3://csdr-public-dev/datasets/gmw-v3/0-0-1/raw \
		--source-zip-name=gmw_v3_1996_gtiff.zip \
		--target-location=s3://csdr-public-dev/datasets/gmw-v3/0-0-1/data \
		--overwrite

## Index v3
# This is recursive over all subfolders (one for each year). Makes just one STAC-geoparquet for all years.
index-gmw-v3-local:
	csdr gmw index \
		--source-location=$(PWD)/cache/datasets/gmw-v3/0-0-1/data \
		--target-location=$(PWD)/cache/datasets/gmw-v3/0-0-1 \
		--overwrite

index-gmw-v3-s3:
	csdr gmw index \
		--source-location=s3://csdr-public-dev/datasets/gmw-v3/0-0-1/data \
		--target-location=s3://csdr-public-dev/datasets/gmw-v3/0-0-1 \
		--overwrite

## Provenance v3
# Make a Dataset in the app and use the ID here
provenance-gmw-v3-local-db:
	csdr provenance dataset \
		--id=36fff098-96f9-4b98-b728-10b2d71a4149 \
		--dataset-url=./cache/datasets/gmw-v3/0-0-1/gmw.parquet \
		--source-url="https://zenodo.org/records/6894273" \
		--source-metadata-url="https://zenodo.org/records/6894273" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite

provenance-gmw-v3-s3-db:
	csdr provenance dataset \
		--id=36fff098-96f9-4b98-b728-10b2d71a4149 \
		--dataset-url=s3://csdr-public-dev/datasets/gmw-v3/0-0-1/gmw.parquet \
		--source-url="https://zenodo.org/records/6894273" \
		--source-metadata-url="https://zenodo.org/records/6894273" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite


# Dataset Seagrass
dataset-seagrass-index-local:
	csdr seagrass index \
		--source-location=s3://dep-public-data/dep_s2_seagrass/0-2-0 \
		--target-location=./cache/datasets/seagrass/0-0-1 \
		--overwrite
dataset-seagrass-index-s3:
	csdr seagrass index \
		--source-location=s3://dep-public-data/dep_s2_seagrass/0-2-0 \
		--target-location=s3://csdr-public-dev/datasets/seagrass/0-0-1 \
		--overwrite

dataset-seagrass-provenance-local:
	csdr provenance dataset \
		--id=8faf443a-3b57-47f8-8a7c-e9fbb00ca84c \
		--dataset-url=./cache/datasets/seagrass/0-0-1/dep_s2_seagrass.parquet \
		--source-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--source-metadata-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite
dataset-seagrass-provenance-s3:
	csdr provenance dataset \
		--id=8faf443a-3b57-47f8-8a7c-e9fbb00ca84c \
		--dataset-url=s3://csdr-public-dev/datasets/seagrass/0-0-1/dep_s2_seagrass.parquet \
		--source-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--source-metadata-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite


# Dataset ACA - reef extent
dataset-aca-extract-local:
	csdr aca extract \
		--source-location=s3://csdr-public-dev/datasets/aca/0-0-1/raw \
		--target-location=./cache/datasets/aca/0-0-1/data \
		--no-overwrite
# 		--overwrite

dataset-aca-index-local:
	csdr aca index \
		--source-location=./cache/datasets/aca/0-0-1/data \
		--target-location=./cache/datasets/aca/0-0-1 \
		--overwrite

dataset-aca-provenance-local-db:
	csdr provenance dataset \
		--id=7c8c93d3-e5a0-4726-8da4-b00dfbe866a6 \
		--dataset-url=./cache/datasets/aca/0-0-1/reefextent.parquet \
		--source-url="https://allencoralatlas.org/atlas/" \
		--source-metadata-url="https://storage.googleapis.com/coral-atlas-static-files/download-package-materials/Class-Descriptions-Benthic-Maps-v3.pdf" \
		--dataset-type=geoparquet \
		--post-to-database \
		--overwrite

# Dataset MS Buildings
dataset-buildings-extract-local:
	csdr buildings extract \
		--source-location=https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country \
		--target-location=./cache/datasets/buildings/0-0-1/data \
		--no-overwrite \
		--max-concurrent=16

dataset-buildings-index-local:
	csdr buildings index \
		--source-location=./cache/datasets/buildings/0-0-1/data \
		--target-location=./cache/datasets/buildings/0-0-1 \
		--overwrite

dataset-buildings-provenance-local-db:
	csdr provenance dataset \
		--id=608e20ef-b074-47aa-a0f7-c0eb5437d28b \
		--dataset-url=./cache/datasets/buildings/0-0-1/buildings.parquet \
		--source-url="https://source.coop/vida/google-microsoft-open-buildings" \
		--source-metadata-url="https://source.coop/vida/google-microsoft-open-buildings" \
		--dataset-type=geoparquet \
		--post-to-database \
		--overwrite


### GEOMETRIES ###

# Geometry EEZ
### EEZ cache
geometry-eez-cache-local:
	csdr eez cache \
		--target-location=./cache/geometries/eez-v4/0-0-1/raw \
		--overwrite

geometry-eez-cache-s3:
	csdr eez cache \
		--target-location=s3://csdr-public-dev/geometries/eez-v4/0-0-1/raw \
		--overwrite
		
### EEZ convert
geometry-eez-convert-local:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location ./cache/geometries/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name=EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location=./cache/geometries/eez-v4/0-0-1/runs/test-run-id \
		--create-pmtiles

geometry-eez-convert-s3:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location=s3://csdr-public-dev/geometries/eez-v4/0-0-1/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name=EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id \
		--create-pmtiles

### EEZ provenance
geometry-eez-provenance-local:
	csdr provenance geometry \
		--id=6231cc07-5723-4c95-8e64-39322a9be2ed \
		--run-id=test-run-id \
		--geometry-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--geometry-type=geoparquet \
		--overwrite

geometry-eez-provenance-local-db:
	csdr provenance geometry \
		--id=6231cc07-5723-4c95-8e64-39322a9be2ed \
		--run-id=test-run-id \
		--geometry-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--geometry-type=geoparquet \
		--post-to-database \
		--post-geometry-outputs \
		--overwrite

geometry-eez-provenance-s3-db:
	csdr provenance geometry \
		--id=6231cc07-5723-4c95-8e64-39322a9be2ed \
		--run-id=test-run-id \
		--geometry-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--geometry-type=geoparquet \
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

# Product GMW EEZ V4 List Geometries
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
		--run-id=test-product-gmw-v4-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v4-eez/0-0-1/runs/test-product-gmw-v4-eez-run-id \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=1cd8d5a6-8ba2-537a-9706-a6413e025b03 \
		--overwrite

product-gmw-v4-eez-process-geometry-s3:
	csdr products process-geometry \
		--product-id=f7cf7d28-9e39-4e3c-8102-705fc3eb40a0 \
		--run-id=test-product-gmw-v3-eez-run-id \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--target-location=s3://csdr-public-dev/products/gmw-v4-eez/0-0-1/runs/test-product-gmw-v3-eez-run-id \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=197afcdf-b98b-5e5c-bd04-0e0a8cbe8386 \
		--overwrite
# 		--dataset-provenance-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \


# Process all geometries with Dask. The workflow does not use this but it is helpful for developing with Dask locally
product-gmw-v4-eez-process-all-geometries-dask-s3:
	csdr products process-all-geometries-dask \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--run-id=test-product-gmw-v4-eez-run-id \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--target-location=s3://csdr-public-dev/products/gmw-v4-eez/0-0-1/runs/test-product-gmw-v4-eez-run-id \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--overwrite \
		--use-dask \
		--dask-opts="n_workers=8,threads_per_worker=1,memory_limit=3GB"

product-gmw-v4-eez-process-all-geometries-dask-local:
	csdr products process-all-geometries-dask \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--run-id=test-product-gmw-v4-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v4-eez/0-0-1/runs/test-product-gmw-v4-eez-run-id \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime=2024 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--overwrite \
		--use-dask \
		--dask-opts="n_workers=8,threads_per_worker=1,memory_limit=3GB"

# Product GMW v4 EEZ Consolidate
product-gmw-v4-eez-consolidate-local:
	csdr products consolidate \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--location=./cache/products/gmw-v4-eez/0-0-1/runs/test-product-gmw-v4-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--variable-name=mangrove \
		--datetime=2024

product-gmw-v4-eez-consolidate-s3:
	csdr products consolidate \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--location s3://csdr-public-dev/products/gmw-v4-eez/0-0-1/runs/test-product-gmw-v4-eez-run-id \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-product-gmw-v4-eez-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/0-0-1/gmw.parquet.provenance.json \
		--variable-name=mangrove \
		--datetime=2024

# You need to make a Product in the app before running provenance. Use that product ID here.
# You also need to make a Variable. It must have the ID 'sum-area-by-value'.
product-gmw-v4-eez-provenance-local-db:
	csdr provenance product \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--product-url=./cache/products/gmw-v4-eez/0-0-1/runs/test-product-gmw-v4-eez-run-id/mangrove/935e9c13-7e2e-40c5-a4f8-f5f62ea54381.parquet \
		--run-id=test-product-gmw-v4-eez-run-id \
		--dataset-run-id=dc364a0b-a719-4a39-b088-653dd28bb7a6 \
		--geometries-run-id=755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--post-to-database \
		--overwrite

product-gmw-v4-eez-provenance-s3-db:
	csdr provenance product \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--product-url=s3://csdr-public-dev/products/gmw-v4-eez/0-0-1/runs/test-product-gmw-v4-eez-run-id/mangrove/935e9c13-7e2e-40c5-a4f8-f5f62ea54381.parquet \
		--run-id=test-product-gmw-v4-eez-run-id \
		--dataset-run-id=dc364a0b-a719-4a39-b088-653dd28bb7a6 \
		--geometries-run-id=755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--post-to-database \
		--overwrite


### Product GMW v3 by EEZ ###

# Lists the same geometries as GMW v4 EEZ
product-gmw-v3-eez-list-geometries-local:
	csdr products list-geometries \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=./cache/tmp/geometries_list.json

product-gmw-v3-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=ae9b3100-611b-4841-97f0-d63c3dda0637 \
		--run-id=test-product-gmw-v3-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v3/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v3-eez/0-0-1/runs/test-product-gmw-v3-eez-run-id \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime-string-match=1996 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--geometry-id=01ff6be8-675b-5c8e-97dc-8cb224a12db6 \
		--overwrite

product-gmw-v3-eez-process-all-geometries-dask-local:
	csdr products process-all-geometries-dask \
		--product-id=ae9b3100-611b-4841-97f0-d63c3dda0637 \
		--run-id=test-product-gmw-v3-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v3/0-0-1/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v3-eez/0-0-1/runs/test-product-gmw-v3-eez-run-id \
		--variable-name=mangrove \
		--variable-value=1.0 \
		--datetime-string-match=2020 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--overwrite \
		--use-dask \
		--dask-opts="n_workers=8,threads_per_worker=1,memory_limit=3GB"
# 		--datetime-string-match=2020 \

product-gmw-v3-eez-consolidate-local:
	csdr products consolidate \
		--product-id=ae9b3100-611b-4841-97f0-d63c3dda0637 \
		--location=./cache/products/gmw-v3-eez/0-0-1/runs/test-product-gmw-v3-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v3/0-0-1/gmw.parquet.provenance.json \
		--variable-name=mangrove
# No datetime because there are many

product-gmw-v3-eez-provenance-local-db:
	csdr provenance product \
		--product-id=ae9b3100-611b-4841-97f0-d63c3dda0637 \
		--product-url=./cache/products/gmw-v3-eez/0-0-1/runs/test-product-gmw-v3-eez-run-id/mangrove/ae9b3100-611b-4841-97f0-d63c3dda0637.parquet \
		--run-id=test-product-gmw-v3-eez-run-id \
		--dataset-run-id=d97e1dd1-a9eb-481b-9e17-30fdc1fe6838 \
		--geometries-run-id=test-run-id \
		--post-to-database \
		--overwrite


### Product Seagrass EEZ v4 ###

# Lists the same geometries as GMW v4 EEZ but for seagrass product
product-seagrass-eez-list-geometries-local:
	csdr products list-geometries \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=./cache/tmp/geometries_list.json

# We need to run this for each year (just like we do for GMW v3). Seagrass has 2017-2024.
# Seagrass: STAC-Parquet is 4326, but STAC items are 3832.
# EEZ is 4326.
product-seagrass-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=e302f96a-e8bb-4457-a55a-4010d98e0a47 \
		--run-id=test-product-seagrass-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/seagrass/0-0-1/dep_s2_seagrass.parquet.provenance.json \
		--target-location=./cache/products/seagrass-eez/0-0-1/runs/test-product-seagrass-eez-run-id \
		--variable-name=seagrass \
		--variable-value=1 \
		--datetime-string-match="2017" \
		--load-kwargs="resolution=100,crs=epsg:3832" \
		--geometry-id=1d7022dd-e6de-50b5-bee5-687df14be0a2 \
		--overwrite

# We need to call this for 2017-2024 to process all seagrass data
product-seagrass-eez-process-all-geometries-dask-local:
	csdr products process-all-geometries-dask \
		--product-id=e302f96a-e8bb-4457-a55a-4010d98e0a47 \
		--run-id=test-product-seagrass-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/seagrass/0-0-1/dep_s2_seagrass.parquet.provenance.json \
		--target-location=./cache/products/seagrass-eez/0-0-1/runs/test-product-seagrass-eez-run-id \
		--variable-name=seagrass \
		--variable-value=1 \
		--datetime-string-match="2021" \
		--load-kwargs="resolution=500,crs=epsg:3832" \
		--overwrite \
		--use-dask \
		--dask-opts="n_workers=8,threads_per_worker=1,memory_limit=3GB"

product-seagrass-eez-consolidate-local:
	csdr products consolidate \
			--product-id=e302f96a-e8bb-4457-a55a-4010d98e0a47 \
			--location=./cache/products/seagrass-eez/0-0-1/runs/test-product-seagrass-eez-run-id \
			--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
			--dataset-provenance-url=./cache/datasets/seagrass/0-0-1/dep_s2_seagrass.parquet.provenance.json \
			--variable-name=seagrass			

product-seagrass-eez-provenance-local-db:
	csdr provenance product \
		--product-id=e302f96a-e8bb-4457-a55a-4010d98e0a47 \
		--product-url=./cache/products/seagrass-eez/0-0-1/runs/test-product-seagrass-eez-run-id/seagrass/e302f96a-e8bb-4457-a55a-4010d98e0a47.parquet \
		--run-id=test-product-seagrass-eez-run-id \
		--dataset-run-id=1a045bf6-9deb-42d4-8150-9ce460e5f2a2 \
		--geometries-run-id=test-run-id \
		--post-to-database \
		--overwrite


# Product ACA Reef Extent by EEZ
product-aca-reef-extent-eez-list-geometries-local:
	csdr products list-geometries \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=./cache/tmp/geometries_list.json

# I think there is only one year of ACA reef extent data, so no need for datetime string match. It is 2022 I believe.
# This is different to other products because the geometry and dataset are both vector (parquet), rather than the dataset being raster.
# geometry: Nauru: 1d7022dd-e6de-50b5-bee5-687df14be0a2 - has reef areas
# geometry: South Sudan: b1b00b2e-2739-5215-a18c-eb72c5798034 - does not have reef areas
 # There is only one year of data. We need to pass datetime anyway so the folder structure is the same as other products.
product-aca-reef-extent-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=5926571e-a088-419d-a966-24557866ce90 \
		--run-id=test-product-reef-extent-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/aca/0-0-1/reefextent.parquet.provenance.json \
		--target-location=./cache/products/aca-reef-extent-eez/0-0-1/runs/test-product-reef-extent-eez-run-id \
		--variable-name=class \
		--variable-value=Reef \
		--datetime=2022 \
		--geometry-id=1d7022dd-e6de-50b5-bee5-687df14be0a2 \
		--overwrite

# TODO: Add S3 version to test Sedona connect to S3.
# product-aca-reef-extent-eez-process-geometry-s3-and-local:
# 	csdr products process-geometry \
# 		--product-id=5926571e-a088-419d-a966-24557866ce90 \
# 		--run-id=test-product-reef-extent-eez-run-id \
# 		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
# 		--dataset-provenance-url=s3://csdr-public-dev/datasets/aca/0-0-1/reefextent.parquet.provenance.json \
# 		--target-location=s3://csdr-public-dev/products/aca-reef-extent-eez/0-0-1/runs/test-product-reef-extent-eez-run-id \
# 		--variable-name=class \
# 		--variable-value=Reef \
# 		--datetime=2022 \
# 		--geometry-id=b1b00b2e-2739-5215-a18c-eb72c5798034 \
# 		--overwrite

# Just one year of data.
 # There is only one year of data. We need to pass datetime anyway so the folder structure is the same as other products.
product-aca-reef-extent-eez-process-all-geometries-dask-local:
	csdr products process-all-geometries-dask \
		--product-id=5926571e-a088-419d-a966-24557866ce90 \
		--run-id=test-product-reef-extent-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/aca/0-0-1/reefextent.parquet.provenance.json \
		--target-location=./cache/products/aca-reef-extent-eez/0-0-1/runs/test-product-reef-extent-eez-run-id \
		--variable-name=class \
		--variable-value=Reef \
		--datetime=2022 \
		--overwrite \
		--use-dask \
		--dask-opts="n_workers=8,threads_per_worker=1,memory_limit=3GB"

product-aca-reef-extent-eez-consolidate-local:
	csdr products consolidate \
			--product-id=5926571e-a088-419d-a966-24557866ce90 \
			--location=./cache/products/aca-reef-extent-eez/0-0-1/runs/test-product-reef-extent-eez-run-id \
			--geometry-provenance-url=./cache/geometries/eez-v4/0-0-1/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
			--dataset-provenance-url=./cache/datasets/aca/0-0-1/reefextent.parquet.provenance.json \
			--variable-name=class
# 'class' isn't the best variable-name here. 'reefextent' would be better. 'class' is just the column name used in process_geometry.

product-aca-reef-extent-eez-provenance-local-db:
	csdr provenance product \
		--product-id=5926571e-a088-419d-a966-24557866ce90 \
		--product-url=./cache/products/aca-reef-extent-eez/0-0-1/runs/test-product-reef-extent-eez-run-id/class/5926571e-a088-419d-a966-24557866ce90.parquet \
		--run-id=test-product-reef-extent-eez-run-id \
		--dataset-run-id=1a045bf6-9deb-42d4-8150-9ce460e5f2a2 \
		--geometries-run-id=test-run-id \
		--post-to-database \
		--overwrite


# Product buildings by EEZ
# Count how many buildings per EEZ.
# product-buildings-eez-process-geometry-local:
# 	csdr products process-geometry \


### OTHER ###

# Test GeoJSON
geometry-geojson-convert:
	csdr convert geo-to-parquet \
		--source-location=tests/data/single_geometry.geojson \
		--target-location=tests/data \
		--name-field=name \
		--overwrite

geometry-geojson-provenance:
	csdr provenance geometry \
		--geometry-url=tests/data/single_geometry.parquet \
		--geometry-type=geoparquet \
		--id=65243c8f-355d-4b36-bd96-72de8c6f1bff \
		--source-metadata-url=https://thing.com \
		--post-to-database \
		--post-geometry-outputs \
		--no-post-geometry-in-bulk
