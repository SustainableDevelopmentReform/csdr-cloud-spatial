To get running locally first follow: /Users/wj/Projects/csdr/csdr-cloud-spatial/docs/simple-cli-run-local-instructions.md

To get app running locally follow https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial-app/blob/run-app-docs/README.md

# Create Geometry

Go to http://localhost:3000/console/geometries or https://csdr.dev.oceandevelopmentdata.org/console/geometries.
Sign in if not already signed in.
Click add Geometry. Leave id blank so it auto-populates with uuid. Add a descriptive name e.g. "Global Exclusive Economic Zones". Add a description e.g. "Marineregions: the intersect of the Exclusive Economic Zones and IHO areas.".
Metadata: Test Metadata
Source URL: https://marineregions.org/sources.php
Source Metadata URL: https://marineregions.org/download_file.php?name=EEZ_land_union_v4_202410.zip

Now run this workflow (WIP): https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial-flux/blob/test-geometries-run-id-path/workflows/dev/templates/geometries-eez-v4.yaml
Need to test this for PMTiles writing.
