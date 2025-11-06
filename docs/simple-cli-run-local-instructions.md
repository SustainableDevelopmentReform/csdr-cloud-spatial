# Install dependencies and CLI (recommended via Docker)

### Clone the repository

`git clone https://github.com/SustainableDevelopmentReform/csdr-cloud-spatial.git`

`cd csdr-cloud-spatial`

### Build the Docker image

`docker build -t csdr-cloud-spatial .`


### Start the shell without mounting

`docker run --rm -it csdr-cloud-spatial /bin/bash`

### In the shell add env vars

export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_SESSION_TOKEN="..."
export AWS_DEFAULT_REGION=ap-southeast-2

### In the shell run some csdr commands

csdr seagrass debug --no-overwrite
csdr seagrass debug --overwrite
csdr seagrass index-dep --no-overwrite
csdr seagrass index-dep --target_location s3://csdr-public-dev/datasets/seagrass/0-0-1 --no-overwrite

You can alternatively do:

`python -m csdr.cli version` or `csdr version`



Todo: try run the shell so that it mounts the local file system so that files can be edited and commands rerun. e.g. `docker run --rm -it -v "$PWD":/code csdr-cloud-spatial /bin/bash`
--rm means remove container when it exits.
-it means interactive with TTY shell prompt.
-v "$PWD":/code means to mount the current working directory on my machine to /app in the container. This means that all my code is available there.
csdr-cloud-spatial: Use the Docker image you built earlier
/bin/bash means start a bash shell inside the container

I was having issues with this overwriting the container file system.
