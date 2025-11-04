# Install dependencies and CLI (recommended via Docker)

### Clone the repository
`git clone https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial.git`

`cd csdr-cloud-spatial`

### Build the Docker image
`docker build -t csdr-cloud-spatial .`

### Start a shell with the CLI available. Mount local file system into container
`docker run --rm -it -v "$PWD":/code csdr-cloud-spatial bash`

# Run Seagrass commands using Makefile targets

### To index Seagrass dataset
`make dataset-seagrass-index`

### To generate provenance for Seagrass dataset
`make dataset-seagrass-provenance`
