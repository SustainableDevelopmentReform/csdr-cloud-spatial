To get running locally first follow: /Users/wj/Projects/csdr/csdr-cloud-spatial/docs/simple-cli-run-local-instructions.md

To get app running locally follow https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial-app/blob/run-app-docs/README.md

# Create Product

Go to http://localhost:3000/console/product or https://csdr.dev.oceandevelopmentdata.org/console/product.
Sign in if not already signed in.
Click add Product. Leave id blank so it auto-populates with uuid. Add a descriptive name e.g. "DEP Seagrass by Exclusive Economic Zones". Add a description e.g. "This is the DEP Seagrass per EEZ. This dataset is not global so a lot of EEZs will not have any seagrass.".
Metadata: Test Metadata
Time Precision: ?? Does the Seagrass have multiple timepoints?

Now run this workflow (actually need to make a Seagrass x EEZ product https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial-flux/blob/test-geometries-run-id-path/workflows/dev/templates/product-gmw-v3-eez.yaml): 


Time precision? It would be nice in the dataset to see the temporal range and resolution. Geometries could change over time too. The product is analysing a potentially massive time range of a dataset against a static snapshot of time for the geometries.
