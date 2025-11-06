To get running locally first follow: /Users/wj/Projects/csdr/csdr-cloud-spatial/docs/simple-cli-run-local-instructions.md

To get app running locally follow https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial-app/blob/run-app-docs/README.md

# Create Dataset

Go to http://localhost:3000/console/dataset or https://csdr.dev.oceandevelopmentdata.org/console/dataset.
Sign in if not already signed in.
Click add Dataset. Leave id blank so it auto-populates with uuid. Add a descriptive name e.g. "Digital Earth Pacific Seagrass Extents". Add a description e.g. "This dataset is for the Pacific, not global.".
Metadata: Test Metadata
Source URL: https://data.digitalearthpacific.org/#dep_s2_seagrass/0-2-0/
Source Metadata URL: https://digitalearthpacific.org/#/applications:~:text=Seagrass%20Extents%20(alpha)

Now run this workflow (WIP): https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial-flux/blob/test-geometries-run-id-path/workflows/dev/templates/geometries-eez-v4.yaml
Need to test this for PMTiles writing.
This workflow also creates provenance.