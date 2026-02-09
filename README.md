# CSDR Cloud Spatial Data Repository

This project is... TODO: Complete basic description.

An earlier version of this project used Data Version Control (DVC). There may be minor artifacts remaining.

## Installation

Create Python environment with GDAL and install the dependencies

First, ensure that GDAL is installed in your environment. To install the dependencies for this project, you can use pip with the pyproject.toml file:

### Using pip

```bash
# Install directly from pyproject.toml
pip install -e .
```

### Using Poetry

If you prefer using Poetry for dependency management:

```bash
# Install Poetry if you don't have it already
pip install poetry

# Install dependencies
poetry install
```

## Development

For development purposes, you can install the package with development dependencies:

```bash
# Using pip
pip install -e ".[dev]"

# Using Poetry
poetry install --with dev
```


### Pre commit hooks

Formats Python, YAML, and JSON.

To use pre-commit to automatically run ruff, mypy and other checks on each commit, make sure the development dependencies are installed and then run:

`pre-commit install`

Note that you will need to run `pre-commit run --all-files` if any of the hooks in `.pre-commit-config.yaml` change.


## Pipeline

TODO: Detail this section.

See the Makefile in this project for commands.
Reads/writes locally or to S3.
Please see here for docs on geometries, datasets, and products.
Provenance.


## Build and Push Workflow

This workflow builds a Docker container image and pushes it to Amazon ECR in two scenarios:

- **Main Branch:** A merge to `main` automatically triggers the workflow. The image is tagged with a version (based on git tags) and additionally gets a `latest` tag.

- **Manual Trigger:** Developers can manually trigger the workflow (using `workflow_dispatch`) to create and push a test build from their feature branch without the `latest` tag.

**Note:** If you build from a feature branch, you will need to visit AWS Batch and create a new revision of the job definition `csdr-dev-env-csdr-cloud-spatial` that uses your custom container image tag.

## Building and Running the Docker Image

To build the Docker image locally using [Buildx](https://docs.docker.com/buildx/working-with-buildx/), run:

```bash
docker buildx build . --tag csdr-cloud-spatial:latest
```

Once built, you can run the container:

```bash
docker run -it --rm
  -e GIT_USER_NAME="Your Name"
  -e GIT_USER_EMAIL="your_email@example.com"
  -e GIT_DEPLOY_KEY_B64="base64-encoded-private-key"
  csdr-cloud-spatial:latest
```

### Providing GitHub Deploy Key

This project requires access to a private GitHub repository (`git@github.com:SustainableDevelopmentReform/csdr-cloud-spatial.git`) during execution. This is managed through a **GitHub Deploy Key**, stored in **base64 format**.

You can provide the key in two ways:

1. **Via Environment Variable (Local Development):**

   ```bash
   export GIT_DEPLOY_KEY_B64=$(base64 -w 0 ~/.ssh/csdr-cloud-spatial-deploy-key)
   docker run -e GIT_DEPLOY_KEY_B64="$GIT_DEPLOY_KEY_B64" csdr-cloud-spatial:latest
   ```

2. **Via AWS Secrets Manager (Production / AWS Batch):**

   If `GIT_DEPLOY_KEY_B64` is not provided as an environment variable, the container will attempt to fetch the key from AWS Secrets Manager. The secret should be in this format:

   ```json
   {
     "private_key_b64": "BASE64_STRING_FOR_PRIVATE_KEY",
     "public_key_b64": "BASE64_STRING_FOR_PUBLIC_KEY"
   }
   ```

   Make sure your container's IAM role has permission to read the secret:

   ```json
   {
     "Effect": "Allow",
     "Action": "secretsmanager:GetSecretValue",
     "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:csdr/github-deploy-key-b64"
   }
   ```
