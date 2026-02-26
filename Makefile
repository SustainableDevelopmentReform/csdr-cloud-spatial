# This makefile holds many test commands for working with datasets, geometries and products.
# It is the base for creating the workflows in the -flux repo.
# See here to get developing: ./how_to_run_dataset_eez-v4_workflow.md

# TODO: Check all datetime vs. datetime-string-match. datetime-string-match actually filters, datetime just makes the output path.

VERSION ?= 0.0.1

### DATASETS ###

# Dataset GMW v4
cache-gmw-v4-local:
	csdr gmw cache \
		--source-locations=https://files.auspatious.com/gmw-v4/raw/gmw_mng_2020_v4019_gtiff.zip \
		--target-location=./cache/datasets/gmw-v4/$(VERSION)/raw \
		--out-file=/tmp/cached_files.json \
		--overwrite

cache-gmw-v4-s3:
	csdr gmw cache \
		--source-locations=https://files.auspatious.com/gmw-v4/raw/gmw_mng_2020_v4019_gtiff.zip \
		--target-location=s3://csdr-public-dev/datasets/gmw-v4/$(VERSION)/raw \
		--out-file=/tmp/cached_files.json \
		--overwrite

# Extracting takes a few minutes
# Extract target-location must be an absolute path (for local store)! Otherwise STAC items will be made with broken href attributes.
extract-gmw-v4-local:
	csdr gmw extract \
		--source-location=./cache/datasets/gmw-v4/$(VERSION)/raw \
		--source-zip-name=gmw_mng_2020_v4019_gtiff.zip \
		--target-location=$(PWD)/cache/datasets/gmw-v4/$(VERSION)/data \
		--overwrite

extract-gmw-v4-s3:
	csdr gmw extract \
		--source-location=s3://csdr-public-dev/datasets/gmw-v4/$(VERSION)/raw \
		--source-zip-name=gmw_mng_2020_v4019_gtiff.zip \
		--target-location=s3://csdr-public-dev/datasets/gmw-v4/$(VERSION)/data \
		--overwrite

# TODO: Check whether index source and target locations must be absolute paths (for local store) too for STAC hrefs to be correct. Using absolute paths just in case.
index-gmw-v4-local:
	csdr gmw index \
		--source-location=$(PWD)/cache/datasets/gmw-v4/$(VERSION)/data \
		--target-location=$(PWD)/cache/datasets/gmw-v4/$(VERSION) \
		--overwrite

index-gmw-v4-s3:
	csdr gmw index \
		--source-location=s3://csdr-public-dev/datasets/gmw-v4/$(VERSION)/data \
		--target-location=s3://csdr-public-dev/datasets/gmw-v4/$(VERSION) \
		--overwrite

# Make a Dataset in the app and use the ID here
provenance-gmw-v4-local-db:
	csdr provenance dataset \
		--id=5714917f-3549-4a95-9fc4-ff96efbdf311 \
		--dataset-url=./cache/datasets/gmw-v4/$(VERSION)/gmw.parquet \
		--source-url="https://zenodo.org/records/12756047" \
		--source-metadata-url="https://zenodo.org/records/12756047" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite

provenance-gmw-v4-s3-db:
	csdr provenance dataset \
		--id=5714917f-3549-4a95-9fc4-ff96efbdf311 \
		--dataset-url=s3://csdr-public-dev/datasets/gmw-v4/$(VERSION)/gmw.parquet \
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
		--target-location=./cache/datasets/gmw-v3/$(VERSION)/raw \
		--out-file=/tmp/cached_files.json \
		--overwrite
# Many files/years
# Not sure if we should use the Zenodo or Auspatious links.
# https://zenodo.org/records/6894273/files/gmw_v3_1996_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2007_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2008_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2009_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2010_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2015_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2016_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2017_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2018_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2019_gtiff.zip,https://zenodo.org/records/6894273/files/gmw_v3_2020_gtiff.zip
# https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2007_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2008_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2009_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2010_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2015_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2016_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2017_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2018_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2019_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2020_gtiff.zip
cache-gmw-v3-local-multiple-files:
	csdr gmw cache \
		--source-locations=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2007_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2008_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2009_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2010_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2015_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2016_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2017_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2018_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2019_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2020_gtiff.zip \
		--target-location=./cache/datasets/gmw-v3/$(VERSION)/raw \
		--out-file=/tmp/cached_files.json \
		--no-overwrite

cache-gmw-v3-s3-multiple-files:
	csdr gmw cache \
		--source-locations=https://files.auspatious.com/gmwv3/gmw_v3_1996_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2007_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2008_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2009_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2010_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2015_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2016_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2017_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2018_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2019_gtiff.zip,https://files.auspatious.com/gmwv3/gmw_v3_2020_gtiff.zip \
		--target-location=s3://csdr-public-dev/datasets/gmw-v3/$(VERSION)/raw \
		--out-file=/tmp/cached_files.json \
		--no-overwrite

# Example output of cache: ["./cache/datasets/gmw-v3/raw/gmw_v3_1996_gtiff.zip","./cache/datasets/gmw-v3/raw/gmw_v3_2020_gtiff.zip"]
# Example output of list-file-names: ["gmw_v3_1996_gtiff.zip", "gmw_v3_2020_gtiff.zip"]

## Extract v3
# Local paths must be absolute for STAC hrefs to be correct!!
extract-gmw-v3-local:
	csdr gmw extract \
		--source-location=./cache/datasets/gmw-v3/$(VERSION)/raw \
		--source-zip-name=gmw_v3_1996_gtiff.zip \
		--target-location=$(PWD)/cache/datasets/gmw-v3/$(VERSION)/data \
		--overwrite
# 		--source-zip-name=gmw_v3_2020_gtiff.zip \

extract-gmw-v3-s3:
	csdr gmw extract \
		--source-location=s3://csdr-public-dev/datasets/gmw-v3/$(VERSION)/raw \
		--source-zip-name=gmw_v3_1996_gtiff.zip \
		--target-location=s3://csdr-public-dev/datasets/gmw-v3/$(VERSION)/data \
		--overwrite

## Index v3
# This is recursive over all subfolders (one for each year). Makes just one STAC-geoparquet for all years.
index-gmw-v3-local:
	csdr gmw index \
		--source-location=$(PWD)/cache/datasets/gmw-v3/$(VERSION)/data \
		--target-location=$(PWD)/cache/datasets/gmw-v3/$(VERSION) \
		--overwrite

index-gmw-v3-s3:
	csdr gmw index \
		--source-location=s3://csdr-public-dev/datasets/gmw-v3/$(VERSION)/data \
		--target-location=s3://csdr-public-dev/datasets/gmw-v3/$(VERSION) \
		--overwrite

## Provenance v3
# Make a Dataset in the app and use the ID here
provenance-gmw-v3-local-db:
	csdr provenance dataset \
		--id=36fff098-96f9-4b98-b728-10b2d71a4149 \
		--dataset-url=./cache/datasets/gmw-v3/$(VERSION)/gmw.parquet \
		--source-url="https://zenodo.org/records/6894273" \
		--source-metadata-url="https://zenodo.org/records/6894273" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite

provenance-gmw-v3-s3-db:
	csdr provenance dataset \
		--id=36fff098-96f9-4b98-b728-10b2d71a4149 \
		--dataset-url=s3://csdr-public-dev/datasets/gmw-v3/$(VERSION)/gmw.parquet \
		--source-url="https://zenodo.org/records/6894273" \
		--source-metadata-url="https://zenodo.org/records/6894273" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite


# Dataset Seagrass
dataset-seagrass-index-local:
	csdr seagrass index \
		--stac-api-url=https://stac.prod.digitalearthpacific.io \
		--target-location=./cache/datasets/seagrass/$(VERSION) \
		--overwrite
dataset-seagrass-index-s3:
	csdr seagrass index \
		--stac-api-url=https://stac.prod.digitalearthpacific.io \
		--target-location=s3://csdr-public-dev/datasets/seagrass/$(VERSION) \
		--overwrite

dataset-seagrass-provenance-local:
	csdr provenance dataset \
		--id=8faf443a-3b57-47f8-8a7c-e9fbb00ca84c \
		--dataset-url=./cache/datasets/seagrass/$(VERSION)/dep_s2_seagrass.parquet \
		--source-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--source-metadata-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite
dataset-seagrass-provenance-s3:
	csdr provenance dataset \
		--id=8faf443a-3b57-47f8-8a7c-e9fbb00ca84c \
		--dataset-url=s3://csdr-public-dev/datasets/seagrass/$(VERSION)/dep_s2_seagrass.parquet \
		--source-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--source-metadata-url="https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite


# Dataset ACE - Australian Coastal Ecosystems
dataset-ace-index-local:
	csdr ace index \
		--source-stac-url="https://explorer.dea.ga.gov.au/stac" \
		--stac-collection=ga_s2_coastalecosystems_cyear_3_v1 \
		--target-location=./cache/datasets/ace/$(VERSION) \
		--target-filename=ace \
		--overwrite
dataset-ace-provenance-local:
	csdr provenance dataset \
		--id=19e30180-8512-4cce-b280-fa17bb014578 \
		--dataset-url=./cache/datasets/ace/$(VERSION)/ace.parquet \
		--source-url="https://explorer.dea.ga.gov.au/stac/collections/ga_s2_coastalecosystems_cyear_3_v1" \
		--source-metadata-url="https://knowledge.dea.ga.gov.au/data/product/dea-coastal-ecosystems" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite

# Dataset DEP Pacific Mangrove
dataset-dep-mangrove-index-local:
	csdr ace index \
		--source-stac-url="https://stac.digitalearthpacific.org" \
		--stac-collection=dep_s2_mangroves \
		--target-location=./cache/datasets/dep-mangrove/$(VERSION) \
		--target-filename=dep-mangrove \
		--overwrite
dataset-dep-mangrove-provenance-local:
	csdr provenance dataset \
		--id=6e88d2d4-2cc8-46ab-9741-e7cfc20a6330 \
		--dataset-url=./cache/datasets/dep-mangrove/$(VERSION)/dep-mangrove.parquet \
		--source-url="https://data.digitalearthpacific.org/#dep_s2_mangroves/" \
		--source-metadata-url="https://data.digitalearthpacific.org/#dep_s2_mangroves/" \
		--dataset-type=stac-geoparquet \
		--post-to-database \
		--overwrite


# Dataset ACA - reef extent
dataset-aca-extract-local:
	csdr aca extract \
		--source-location=s3://csdr-public-dev/datasets/aca/$(VERSION)/raw \
		--target-location=./cache/datasets/aca/$(VERSION)/data \
		--no-overwrite
# 		--overwrite

dataset-aca-index-local:
	csdr aca index \
		--source-location=./cache/datasets/aca/$(VERSION)/data \
		--target-location=./cache/datasets/aca/$(VERSION) \
		--overwrite

dataset-aca-index-s3:
	csdr aca index \
		--source-location=s3://csdr-public-dev/datasets/aca/$(VERSION)/data \
		--target-location=s3://csdr-public-dev/datasets/aca/$(VERSION) \
		--overwrite

dataset-aca-provenance-local-db:
	csdr provenance dataset \
		--id=7c8c93d3-e5a0-4726-8da4-b00dfbe866a6 \
		--dataset-url=./cache/datasets/aca/$(VERSION)/reefextent.parquet \
		--source-url="https://allencoralatlas.org/atlas/" \
		--source-metadata-url="https://storage.googleapis.com/coral-atlas-static-files/download-package-materials/Class-Descriptions-Benthic-Maps-v3.pdf" \
		--dataset-type=geoparquet \
		--post-to-database \
		--overwrite

# Dataset MS Buildings
# Index is done in-place in Source Coop.
dataset-buildings-index-local:
	csdr buildings index \
		--target-location=./cache/datasets/buildings/$(VERSION) \
		--overwrite
dataset-buildings-provenance-local-db:
	csdr provenance dataset \
		--id=2e09738e-7b2f-4e0e-b66b-a4e332051c25 \
		--dataset-url=./cache/datasets/buildings/$(VERSION)/buildings.parquet \
		--source-url="https://data.source.coop/vida/google-microsoft-open-buildings/geoparquet/by_country_s2/" \
		--source-metadata-url="https://source.coop/vida/google-microsoft-open-buildings" \
		--dataset-type=geoparquet \
		--post-to-database \
		--overwrite


### GEOMETRIES ###

# Geometry EEZ
### EEZ cache
geometry-eez-cache-local:
	csdr geometries cache \
		--source-url="https://files.auspatious.com/unsw/EEZ_land_union_v4_202410.zip" \
		--target-location=./cache/geometries/eez-v4/$(VERSION)/raw \
		--overwrite

geometry-eez-cache-s3:
	csdr geometries cache \
		--source-url="https://files.auspatious.com/unsw/EEZ_land_union_v4_202410.zip" \
		--target-location=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/raw \
		--overwrite

### EEZ convert
geometry-eez-convert-local:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location ./cache/geometries/eez-v4/$(VERSION)/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name=EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id \
		--create-pmtiles

geometry-eez-convert-s3:
	csdr convert zip-to-parquet \
		--name-field UNION \
		--source-zip-location=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/raw/EEZ_land_union_v4_202410.zip \
		--source-internal-path-name=EEZ_land_union_v4_202410/EEZ_land_union_v4_202410.shp \
		--target-location=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/runs/test-run-id \
		--create-pmtiles

### EEZ provenance
geometry-eez-provenance-local:
	csdr provenance geometry \
		--id=6231cc07-5723-4c95-8e64-39322a9be2ed \
		--run-id=test-run-id \
		--geometry-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--geometry-type=geoparquet \
		--overwrite

geometry-eez-provenance-local-db:
	csdr provenance geometry \
		--id=65427160-c63c-4c24-a4ac-7013940fae9e \
		--run-id=755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--geometry-url=./cache/geometries/eez-v4/$(VERSION)/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=./cache/geometries/eez-v4/$(VERSION)/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.pmtiles \
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
		--geometry-url=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet \
		--pmtiles-url=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.pmtiles \
		--source-url="https://www.marineregions.org/downloads.php" \
		--source-metadata-url="https://www.marineregions.org/downloads.php" \
		--geometry-type=geoparquet \
		--post-to-database \
		--post-geometry-outputs \
		--overwrite

# Geometry Australian Coastal Sediment Compartments - Secondary Compartments
geometry-acsc2-cache-local:
	csdr geometries cache \
		--source-url="https://hub.arcgis.com/api/v3/datasets/2af87180973d44b0b5b73583e3c06957_2/downloads/data?format=shp&spatialRefId=4283&where=1%3D1" \
		--target-location=./cache/geometries/acsc2/$(VERSION)/raw \
		--overwrite

geometry-acsc2-convert-local:
	csdr convert zip-to-parquet \
		--name-field name \
		--source-zip-location=./cache/geometries/acsc2/$(VERSION)/raw/Australian_Coastal_Sediment_Compartments_-_Secondary_Compartments.zip \
		--source-internal-path-name=Australian_Coastal_Sediment_Compartments_-_Secondary_Compartments.shp \
		--target-location=./cache/geometries/acsc2/$(VERSION)/runs/acsc2-test-run-id \
		--create-pmtiles

geometry-acsc2-provenance-local-db:
	csdr provenance geometry \
		--id=452a546e-681e-4187-b3d3-8190a317862c \
		--run-id=acsc2-test-run-id \
		--geometry-url=./cache/geometries/acsc2/$(VERSION)/runs/acsc2-test-run-id/Australian_Coastal_Sediment_Compartments_-_Secondary_Compartments.parquet \
		--pmtiles-url=.cache/geometries/acsc2/$(VERSION)/runs/acsc2-test-run-id/Australian_Coastal_Sediment_Compartments_-_Secondary_Compartments.pmtiles \
		--source-url="https://digital.atlas.gov.au/datasets/digitalatlas::australian-coastal-sediment-compartments-secondary-compartments/explore" \
		--source-metadata-url="https://digital.atlas.gov.au/datasets/digitalatlas::australian-coastal-sediment-compartments-secondary-compartments/about" \
		--geometry-type=geoparquet \
		--post-to-database \
		--post-geometry-outputs \
		--overwrite

# Geometry GA Coastal Waters Areas
geometry-cwa-cache-local:
	csdr geometries cache \
		--source-url="https://hub.arcgis.com/api/v3/datasets/37a401e932544c88828a7d099880afb5_1/downloads/data?format=shp&spatialRefId=4283&where=1%3D1" \
		--target-location=./cache/geometries/cwa/$(VERSION)/raw \
		--overwrite
geometry-cwa-convert-local:
	csdr convert zip-to-parquet \
		--name-field name \
		--source-zip-location=./cache/geometries/cwa/$(VERSION)/raw/CW_1970_1980_Areas.zip \
		--source-internal-path-name=CW_1970_1980_Areas.shp \
		--target-location=./cache/geometries/cwa/$(VERSION)/runs/cwa-test-run-id \
		--create-pmtiles
geometry-cwa-provenance-local-db:
	csdr provenance geometry \
		--id=0d3cea10-b1c2-41d6-8da7-5183f9548d84 \
		--run-id=cwa-test-run-id \
		--geometry-url=./cache/geometries/cwa/$(VERSION)/runs/cwa-test-run-id/CW_1970_1980_Areas.parquet \
		--pmtiles-url=.cache/geometries/cwa/$(VERSION)/runs/cwa-test-run-id/CW_1970_1980_Areas.pmtiles \
		--source-url="https://amsis-geoscience-au.hub.arcgis.com/datasets/geoscience-au::coastal-waters-areas-amb2020/explore" \
		--source-metadata-url="https://amsis-geoscience-au.hub.arcgis.com/datasets/geoscience-au::coastal-waters-areas-amb2020/about" \
		--geometry-type=geoparquet \
		--post-to-database \
		--post-geometry-outputs \
		--overwrite

# Geometry Australian States and Territories
geometry-aus-states-cache-local:
	csdr geometries cache \
		--source-url="https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files/STE_2021_AUST_SHP_GDA2020.zip" \
		--target-location=./cache/geometries/aus-states/$(VERSION)/raw \
		--overwrite
geometry-aus-states-convert-local:
	csdr convert zip-to-parquet \
		--name-field STE_NAME21 \
		--source-zip-location=./cache/geometries/aus-states/$(VERSION)/raw/STE_2021_AUST_SHP_GDA2020.zip \
		--source-internal-path-name=STE_2021_AUST_GDA2020.shp \
		--target-location=./cache/geometries/aus-states/$(VERSION)/runs/aus-states-test-run-id \
		--create-pmtiles
geometry-aus-states-provenance-local-db:
	csdr provenance geometry \
		--id=0b9b8e1a-d20a-41c2-843d-1f2b47d6a512 \
		--run-id=aus-states-test-run-id \
		--geometry-url=./cache/geometries/aus-states/$(VERSION)/runs/aus-states-test-run-id/STE_2021_AUST_GDA2020.parquet \
		--pmtiles-url=./cache/geometries/aus-states/$(VERSION)/runs/aus-states-test-run-id/STE_2021_AUST_GDA2020.pmtiles \
		--source-url="https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files/STE_2021_AUST_SHP_GDA2020.zip" \
		--source-metadata-url="https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files#metadata-for-digital-boundary-files" \
		--geometry-type=geoparquet \
		--post-to-database \
		--post-geometry-outputs \
		--overwrite

geometry-pacific-eez-filter-s3:
	csdr helpers filter-geometries-by-name \
		--source-url=https://csdr-public-dev.s3.ap-southeast-2.amazonaws.com/geometries/eez-v4/$(VERSION)/EEZ_land_union_v4_202410.parquet \
		--target-url=s3://csdr-public-dev/geometries/eez-pacific/$(VERSION)/runs/test-run-id/eez-pacific.parquet \
		--name-fields="csdr-name,SOVEREIGN1,SOVEREIGN2" \
		--geometry-names="American Samoa,Cook Islands,Fiji,French Polynesia,Guam,Kiribati,Marshall Islands,Micronesia,Nauru,New Caledonia,Niue,Northern Mariana Islands,Palau,Papua New Guinea,Pitcairn,Solomon Islands,Samoa,Tokelau,Tonga,Tuvalu,Vanuatu,Wallis and Futuna"
# These are from here https://github.com/digitalearthpacific/dep-tools/blob/main/dep_tools/grids.py
# Only difference is I had to rename 'Pitcairn Islands' to 'Pitcairn'.

### PRODUCTS ###

# Product GMW EEZ V4.
# Steps:
# 1. List geometries - writes a list of geometries to a file from a geometry run provenance.
# 2. Process - processes the geometries for the product. I think this is a heavy step that can be killed by Out of Memory errors. Is it parallelized and distributed using Dask?
# 3. Consolidate - consolidates the processed geometries.
# 4. Provenance - generates provenance information for the product.

# Product GMW EEZ V4 List Geometries
product-gmw-v4-eez-list-geometries-years-local:
	csdr products list-geometries-years \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=./cache/tmp/geometries-years.json \
		--years="2020"
# 		--exclude-ids="1cd8d5a6-8ba2-537a-9706-a6413e025b03,d0b2c60c-e347-5d0d-83fa-24db9db9f558"

product-gmw-v4-eez-list-geometries-years-s3:
	csdr products list-geometries-years \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=s3://csdr-public-dev/products/gmw-v4-eez/$(VERSION)/tmp/geometries-years.json \
		--years="2020"

# Product GMW EEZ V4 Process Geometries
# indicator-value=1.0 for mangrove presence. It is boolean raster with 1 for presence and 0 for absence.
# resolution=100 is 100m. 10 is max for GMW v4.
# https://epsg.io/6933
# Create a Product in the app. Use the product ID below. Select your dataset and geometry, and time as yearly.
# Jordan 1cd8d5a6-8ba2-537a-9706-a6413e025b03. No mangrove (but might intersect bbox?).
# Australia b4c4c411-4daa-57d2-b3f7-fb14ec95d6f2. Has mangrove.
product-gmw-v4-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--run-id=test-product-gmw-v4-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/$(VERSION)/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v4-eez/$(VERSION)/runs/test-product-gmw-v4-eez-run-id \
		--indicators-to-extract='{"sum-mangrove-area": {"indicator-name": "mangrove", "indicator-value": 1.0}}' \
		--datetime=2020 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=b4c4c411-4daa-57d2-b3f7-fb14ec95d6f2 \
		--overwrite

# Australia 0ff9144b-4e90-537f-86ed-7ef6bb94f0a8 # Errors
# Nauru 7b628528-0f25-514a-884f-4d9750acccda # Errors
# Ascension 69c7fbeb-e2a2-5b66-9bff-9f3e9774f661 # Works. Has no spatial intersection.
product-gmw-v4-eez-process-geometry-s3:
	csdr products process-geometry \
		--product-id=f7cf7d28-9e39-4e3c-8102-705fc3eb40a0 \
		--run-id=test-product-gmw-v3-eez-run-id \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/$(VERSION)/gmw.parquet.provenance.json \
		--target-location=s3://csdr-public-dev/products/gmw-v4-eez/$(VERSION)/runs/test-product-gmw-v3-eez-run-id \
		--indicators-to-extract='{"sum-mangrove-area": {"indicator-name": "mangrove", "indicator-value": 1.0}}' \
		--datetime=2020 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=7b628528-0f25-514a-884f-4d9750acccda \
		--overwrite

# Product GMW v4 EEZ Consolidate
product-gmw-v4-eez-consolidate-local:
	csdr products consolidate \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--location=./cache/products/gmw-v4-eez/$(VERSION)/runs/test-product-gmw-v4-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/755206f2-dc2f-5b11-8355-2a86b34f7984/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v4/$(VERSION)/gmw.parquet.provenance.json \
		--indicator-name=mangrove \

product-gmw-v4-eez-consolidate-s3:
	csdr products consolidate \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--location s3://csdr-public-dev/products/gmw-v4-eez/$(VERSION)/runs/test-product-gmw-v4-eez-run-id \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/runs/test-product-gmw-v4-eez-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/gmw-v4/$(VERSION)/gmw.parquet.provenance.json \
		--indicator-name=mangrove \

# You need to make a Product in the app before running provenance. Use that product ID here.
# You also need to make a indicator. It must have the ID 'sum-mangrove-area'.
product-gmw-v4-eez-provenance-local-db:
	csdr provenance product \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--product-url=./cache/products/gmw-v4-eez/$(VERSION)/runs/test-product-gmw-v4-eez-run-id/mangrove/935e9c13-7e2e-40c5-a4f8-f5f62ea54381.parquet \
		--run-id=test-product-gmw-v4-eez-run-id \
		--dataset-run-id=9d2cf140-1d6f-405a-93af-ba1a1dcd7029 \
		--geometries-run-id=755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--post-to-database \
		--overwrite

product-gmw-v4-eez-provenance-s3-db:
	csdr provenance product \
		--product-id=935e9c13-7e2e-40c5-a4f8-f5f62ea54381 \
		--product-url=s3://csdr-public-dev/products/gmw-v4-eez/$(VERSION)/runs/test-product-gmw-v4-eez-run-id/mangrove/935e9c13-7e2e-40c5-a4f8-f5f62ea54381.parquet \
		--run-id=test-product-gmw-v4-eez-run-id \
		--dataset-run-id=dc364a0b-a719-4a39-b088-653dd28bb7a6 \
		--geometries-run-id=755206f2-dc2f-5b11-8355-2a86b34f7984 \
		--post-to-database \
		--overwrite


### Product GMW v3 by EEZ ###

# Lists the same geometries as GMW v4 EEZ
product-gmw-v3-eez-list-geometries-years-local:
	csdr products list-geometries-years \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=./cache/tmp/geometries-years.json \
		--years="1996,2007,2008,2009,2010,2015,2016,2017,2018,2019,2020"

product-gmw-v3-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=ae9b3100-611b-4841-97f0-d63c3dda0637 \
		--run-id=test-product-gmw-v3-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v3/$(VERSION)/gmw.parquet.provenance.json \
		--target-location=./cache/products/gmw-v3-eez/$(VERSION)/runs/test-product-gmw-v3-eez-run-id \
		--indicators-to-extract='{"sum-mangrove-area": {"indicator-name": "mangrove", "indicator-value": 1.0}}' \
		--datetime-string-match=1996 \
		--load-kwargs="resolution=500,crs=epsg:6933" \
		--geometry-id=01ff6be8-675b-5c8e-97dc-8cb224a12db6 \
		--overwrite

product-gmw-v3-eez-consolidate-local:
	csdr products consolidate \
		--product-id=ae9b3100-611b-4841-97f0-d63c3dda0637 \
		--location=./cache/products/gmw-v3-eez/$(VERSION)/runs/test-product-gmw-v3-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/gmw-v3/$(VERSION)/gmw.parquet.provenance.json \
		--indicator-name=mangrove
# No datetime because there are many

product-gmw-v3-eez-provenance-local-db:
	csdr provenance product \
		--product-id=ae9b3100-611b-4841-97f0-d63c3dda0637 \
		--product-url=./cache/products/gmw-v3-eez/$(VERSION)/runs/test-product-gmw-v3-eez-run-id/mangrove/ae9b3100-611b-4841-97f0-d63c3dda0637.parquet \
		--run-id=test-product-gmw-v3-eez-run-id \
		--dataset-run-id=d97e1dd1-a9eb-481b-9e17-30fdc1fe6838 \
		--geometries-run-id=test-run-id \
		--post-to-database \
		--overwrite


### Product Seagrass EEZ v4 ###

# Lists the same geometries as GMW v4 EEZ but for seagrass product
product-seagrass-eez-list-geometries-years-local:
	csdr products list-geometries-years \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=./cache/tmp/geometries-years.json \
		--years="2017,2018,2019,2020,2021,2022,2023,2024"

# We need to run this for each year (just like we do for GMW v3). Seagrass has 2017-2024.
# Seagrass: STAC-Parquet is 4326, but STAC items are 3832.
# EEZ is 4326.
# Nauru 1d7022dd-e6de-50b5-bee5-687df14be0a2. Has seagrass.
# Russia b004a623-4187-5492-b4f5-daaabdfb66e4. No seagrass.
product-seagrass-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=e302f96a-e8bb-4457-a55a-4010d98e0a47 \
		--run-id=test-product-seagrass-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/seagrass/$(VERSION)/dep_s2_seagrass.parquet.provenance.json \
		--target-location=./cache/products/seagrass-eez/$(VERSION)/runs/test-product-seagrass-eez-run-id \
		--indicators-to-extract='{"sum-seagrass-area": {"indicator-name": "seagrass", "indicator-value": 1}}' \
		--datetime-string-match="2017" \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=1d7022dd-e6de-50b5-bee5-687df14be0a2 \
		--overwrite

# d74a98d8-0679-5f6e-aa03-74ba02b41718 Jordan (no seagrass)
# fcff483d-6755-5a58-8cfd-902a0831e998 Nauru (has seagrass)
product-seagrass-eez-process-geometry-read-s3-write-local:
	csdr products process-geometry \
		--product-id=e302f96a-e8bb-4457-a55a-4010d98e0a47 \
		--run-id=test-product-seagrass-eez-run-id \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/runs/1cad60fb-73d3-5f95-a733-6bde395af587/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/seagrass/$(VERSION)/dep_s2_seagrass.parquet.provenance.json \
		--target-location=./cache/products/seagrass-eez/$(VERSION)/runs/test-product-seagrass-eez-run-id \
		--indicators-to-extract='{"sum-seagrass-area": {"indicator-name": "seagrass", "indicator-value": 1}}' \
		--datetime-string-match="2017" \
		--load-kwargs="resolution=100,crs=epsg:3832" \
		--geometry-id=fcff483d-6755-5a58-8cfd-902a0831e998 \
		--overwrite

product-seagrass-eez-consolidate-local:
	csdr products consolidate \
			--product-id=e302f96a-e8bb-4457-a55a-4010d98e0a47 \
			--location=./cache/products/seagrass-eez/$(VERSION)/runs/test-product-seagrass-eez-run-id \
			--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
			--dataset-provenance-url=./cache/datasets/seagrass/$(VERSION)/dep_s2_seagrass.parquet.provenance.json \
			--indicator-name=seagrass

product-seagrass-eez-provenance-local-db:
	csdr provenance product \
		--product-id=e302f96a-e8bb-4457-a55a-4010d98e0a47 \
		--product-url=./cache/products/seagrass-eez/$(VERSION)/runs/test-product-seagrass-eez-run-id/seagrass/e302f96a-e8bb-4457-a55a-4010d98e0a47.parquet \
		--run-id=test-product-seagrass-eez-run-id \
		--dataset-run-id=1a045bf6-9deb-42d4-8150-9ce460e5f2a2 \
		--geometries-run-id=test-run-id \
		--post-to-database \
		--overwrite


# Product ACA Reef Extent by EEZ
product-aca-eez-list-geometries-years-local:
	csdr products list-geometries-years \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--out-file=./cache/tmp/geometries-years.json \
		--years="2022"

# I think there is only one year of ACA reef extent data, so no need for datetime string match. It is 2022 I believe.
# This is different to other products because the geometry and dataset are both vector (parquet), rather than the dataset being raster.
# geometry: Nauru: 1d7022dd-e6de-50b5-bee5-687df14be0a2 - has reef areas
# geometry: South Sudan: b1b00b2e-2739-5215-a18c-eb72c5798034 - does not have reef areas
 # There is only one year of data. We need to pass datetime anyway so the folder structure is the same as other products.
product-aca-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=5926571e-a088-419d-a966-24557866ce90 \
		--run-id=test-aca-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/aca/$(VERSION)/reefextent.parquet.provenance.json \
		--target-location=./cache/products/aca-eez/$(VERSION)/runs/test-aca-eez-run-id \
		--indicators-to-extract='{"sum-reef-area": {"indicator-name": "class", "indicator-value": "Reef"}}' \
		--datetime=2022 \
		--geometry-id=1d7022dd-e6de-50b5-bee5-687df14be0a2 \
		--overwrite

# 6fb63148-8709-5ad7-a76c-c6599d34befb Japan.
product-aca-eez-process-geometry-s3:
	csdr products process-geometry \
		--product-id=5926571e-a088-419d-a966-24557866ce90 \
		--run-id=test-aca-eez-run-id \
		--geometry-provenance-url=s3://csdr-public-dev/geometries/eez-v4/$(VERSION)/runs/1cad60fb-73d3-5f95-a733-6bde395af587/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=s3://csdr-public-dev/datasets/aca/$(VERSION)/reefextent.parquet.provenance.json \
		--target-location=s3://csdr-public-dev/products/aca-eez/$(VERSION)/runs/test-aca-eez-run-id \
		--indicators-to-extract='{"sum-reef-area": {"indicator-name": "class", "indicator-value": "Reef"}}' \
		--datetime=2022 \
		--geometry-id=6fb63148-8709-5ad7-a76c-c6599d34befb \
		--overwrite

product-aca-eez-consolidate-local:
	csdr products consolidate \
		--product-id=5926571e-a088-419d-a966-24557866ce90 \
		--location=./cache/products/aca-eez/$(VERSION)/runs/test-aca-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/aca/$(VERSION)/reefextent.parquet.provenance.json \
		--indicator-name=class
# 'class' isn't the best indicator-name here. 'reefextent' would be better. 'class' is just the column name used in process_geometry.

product-aca-eez-provenance-local-db:
	csdr provenance product \
		--product-id=5926571e-a088-419d-a966-24557866ce90 \
		--product-url=./cache/products/aca-eez/$(VERSION)/runs/test-aca-eez-run-id/class/5926571e-a088-419d-a966-24557866ce90.parquet \
		--run-id=test-aca-eez-run-id \
		--dataset-run-id=1a045bf6-9deb-42d4-8150-9ce460e5f2a2 \
		--geometries-run-id=test-run-id \
		--post-to-database \
		--overwrite


# Product buildings by EEZ
# Count how many buildings per EEZ.
# South Sudan b1b00b2e-2739-5215-a18c-eb72c5798034
# South Sudan geometry hits 11 building parquet bboxes.
# France has a massive bounding box that South Sudan intersects, but 0 buildings actually intersect.
# Germany a5446e2f-eaad-5e91-b6d9-b5c5595f4f3b
# Australia bdcf6908-2ad1-5451-80ef-d0a9994d8a78
# Malaysia 698e177a-687f-5e72-8bd5-280b88d9ad19
# indicator with id 'count-buildings' must exist in the app before running this.
product-buildings-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=f9eef768-40bd-48e5-903d-dc2bb1c16f6d \
		--run-id=test-buildings-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/buildings/$(VERSION)/buildings.parquet.provenance.json \
		--target-location=./cache/products/buildings-eez/$(VERSION)/runs/test-buildings-eez-run-id \
		--datetime=2025 \
		--geometry-id=b1b00b2e-2739-5215-a18c-eb72c5798034 \
		--indicators-to-extract='{"count-buildings": {"indicator-name": "buildings", "indicator-value": null}}' \
		--overwrite
product-buildings-eez-consolidate-local:
	csdr products consolidate \
		--product-id=f9eef768-40bd-48e5-903d-dc2bb1c16f6d \
		--location=./cache/products/buildings-eez/$(VERSION)/runs/test-buildings-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/buildings/$(VERSION)/buildings.parquet.provenance.json \
		--indicator-name=buildings
# 		--indicator-name=count-buildings
product-buildings-eez-provenance-local-db:
	csdr provenance product \
		--product-id=f9eef768-40bd-48e5-903d-dc2bb1c16f6d \
		--product-url=./cache/products/buildings-eez/$(VERSION)/runs/test-buildings-eez-run-id/buildings/f9eef768-40bd-48e5-903d-dc2bb1c16f6d.parquet \
		--run-id=test-buildings-eez-run-id \
		--dataset-run-id=c77dd12e-875b-4d05-b9de-0958f1a4d7ec \
		--geometries-run-id=eez-test-run-id \
		--post-to-database \
		--overwrite


# Product ACEs by EEZ
# First product with many indicators. These must be added in the app as indicators.
# Times: 2021 and 2022.
# Geometries:
# 446b9a00-e0e3-51be-934b-0df1c2c75b2c, b608c6ab-6ce4-5a89-9523-ee07d8dd4c22, 320d51fc-e195-5e45-9c2c-fd4fb38af9c7
product-ace-acsc2-process-geometry-local:
	csdr products process-geometry \
		--product-id=ab3e7b2c-e79e-4f8b-b1f7-64bf44eb1443 \
		--run-id=test-ace-acsc2-run-id \
		--geometry-provenance-url=./cache/geometries/acsc2/$(VERSION)/runs/acsc2-test-run-id/Australian_Coastal_Sediment_Compartments_-_Secondary_Compartments.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/ace/$(VERSION)/ace.parquet.provenance.json \
		--target-location=./cache/products/ace-acsc2/$(VERSION)/runs/test-ace-acsc2-run-id \
		--datetime-string-match='2022-01-01T00:00:00Z/2022-12-31T23:59:59.999Z' \
		--datetime=2022 \
		--load-kwargs="resolution=100,crs=epsg:6933" \
		--geometry-id=b608c6ab-6ce4-5a89-9523-ee07d8dd4c22 \
		--indicators-to-extract='{"sum-mangrove-area": {"indicator-name": "classification", "indicator-value": 3}, "sum-intertidal-area": {"indicator-name": "classification", "indicator-value": 2}, "sum-saltmarsh-area": {"indicator-name": "classification", "indicator-value": 4}, "sum-seagrass-area": {"indicator-name": "classification", "indicator-value": 5}, "percent-mangrove-area": {"indicator-name": null, "indicator-value": null}, "percent-intertidal-area": {"indicator-name": null, "indicator-value": null}, "percent-saltmarsh-area": {"indicator-name": null, "indicator-value": null}, "percent-seagrass-area": {"indicator-name": null, "indicator-value": null}}' \
		--overwrite
product-ace-acsc2-consolidate-local:
	csdr products consolidate \
		--product-id=ab3e7b2c-e79e-4f8b-b1f7-64bf44eb1443 \
		--location=./cache/products/ace-acsc2/$(VERSION)/runs/test-ace-acsc2-run-id \
		--geometry-provenance-url=./cache/geometries/acsc2/$(VERSION)/runs/acsc2-test-run-id/Australian_Coastal_Sediment_Compartments_-_Secondary_Compartments.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/ace/$(VERSION)/ace.parquet.provenance.json \
		--indicator-name=many-indicators
product-ace-acsc2-provenance-local-db:
	csdr provenance product \
		--product-id=ab3e7b2c-e79e-4f8b-b1f7-64bf44eb1443 \
		--product-url=./cache/products/ace-acsc2/$(VERSION)/runs/test-ace-acsc2-run-id/many-indicators/ab3e7b2c-e79e-4f8b-b1f7-64bf44eb1443.parquet \
		--run-id=test-ace-acsc2-run-id \
		--dataset-run-id=b110a9cd-0052-4436-8504-3d55f6d79094 \
		--geometries-run-id=acsc2-test-run-id \
		--post-to-database \
		--overwrite


# Product DEP Mangrove per EEZ
product-dep-mangrove-eez-process-geometry-local:
	csdr products process-geometry \
		--product-id=19b9f140-9d1e-4b53-820f-d9745a3faf1b \
		--run-id=test-dep-mangrove-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/dep-mangrove/$(VERSION)/dep-mangrove.parquet.provenance.json \
		--target-location=./cache/products/dep-mangrove-eez/$(VERSION)/runs/test-dep-mangrove-eez-run-id \
		--datetime-string-match=2024 \
		--geometry-id=183feceb-c245-5b65-ab0a-59f3ad20685c \
		--indicators-to-extract='{"sum-mangrove-area": {"indicator-name": "mangroves", "indicator-value": "1.0,2.0"}}' \
		--overwrite
product-dep-mangrove-eez-consolidate-local:
	csdr products consolidate \
		--product-id=19b9f140-9d1e-4b53-820f-d9745a3faf1b \
		--location=./cache/products/dep-mangrove-eez/$(VERSION)/runs/test-dep-mangrove-eez-run-id \
		--geometry-provenance-url=./cache/geometries/eez-v4/$(VERSION)/runs/test-run-id/EEZ_land_union_v4_202410.parquet.provenance.json \
		--dataset-provenance-url=./cache/datasets/dep-mangrove/$(VERSION)/dep-mangrove.parquet.provenance.json \
		--indicator-name=mangroves
product-dep-mangrove-eez-provenance-local-db:
	csdr provenance product \
		--product-id=19b9f140-9d1e-4b53-820f-d9745a3faf1b \
		--product-url=./cache/products/dep-mangrove-eez/$(VERSION)/runs/test-dep-mangrove-eez-run-id/mangroves/19b9f140-9d1e-4b53-820f-d9745a3faf1b.parquet \
		--run-id=test-dep-mangrove-eez-run-id \
		--dataset-run-id=924a2b90-9ee9-4afb-b585-3f05e0d22e2d \
		--geometries-run-id=eez-test-run-id \
		--post-to-database \
		--overwrite

# Product DEP Mangrove per Pacific EEZ
# product-dep-mangrove-pacific-eez-process-geometry-local:
# TODO
# product-dep-mangrove-pacific-eez-consolidate-local:
# TODO
# product-dep-mangrove-pacific-eez-provenance-local-db:
# TODO


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
