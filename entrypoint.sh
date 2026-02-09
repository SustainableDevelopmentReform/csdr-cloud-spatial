#!/bin/bash

# TODO: Validate that this file is still needed since removing DVC.
# My gut feeling is that it was only needed to commit DVC stuff to git, which we no longer do.
# This entrypoint.sh file is a shell script designed to run as the entrypoint for a Docker container or CI/CD job.

set -euo pipefail

# --- SSH Deploy Key Setup ---
# If GIT_DEPLOY_KEY_B64 is not set, fetch it from AWS Secrets Manager
if [[ -z "${GIT_DEPLOY_KEY_B64:-}" ]]; then
  echo "GIT_DEPLOY_KEY_B64 not provided — fetching from AWS Secrets Manager..."

  # Fetch the secret JSON from AWS Secrets Manager
  secret_json=$(aws secretsmanager get-secret-value \
    --region "${AWS_REGION:-ap-southeast-2}" \
    --secret-id csdr/github-deploy-key-b64 \
    --query SecretString --output text)

  if [[ -z "$secret_json" ]]; then
    echo "ERROR: Failed to fetch deploy key from Secrets Manager" >&2
    exit 1
  fi

  # Extract private_key_b64 from the JSON
  GIT_DEPLOY_KEY_B64=$(echo "$secret_json" | jq -r .private_key_b64)

  if [[ -z "$GIT_DEPLOY_KEY_B64" || "$GIT_DEPLOY_KEY_B64" == "null" ]]; then
    echo "ERROR: private_key_b64 is missing in the secret JSON" >&2
    exit 1
  fi

  export GIT_DEPLOY_KEY_B64
else
  echo "Using GIT_DEPLOY_KEY_B64 provided via environment variable."
fi

# --- SSH Directory and Key Setup ---
# Create SSH directory and set permissions
mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Decode and write the deploy key to id_rsa
echo "$GIT_DEPLOY_KEY_B64" | base64 -d > /root/.ssh/id_rsa
chmod 600 /root/.ssh/id_rsa

# Add GitHub to known hosts for SSH
ssh-keyscan github.com >> /root/.ssh/known_hosts
chmod 644 /root/.ssh/known_hosts

# --- Git Identity Configuration ---
# Set global Git user name and email if provided
if [[ -n "${GIT_USER_NAME:-}" ]]; then
  git config --global user.name "$GIT_USER_NAME"
fi

if [[ -n "${GIT_USER_EMAIL:-}" ]]; then
  git config --global user.email "$GIT_USER_EMAIL"
fi

# Print Git identity for verification
echo "Using Git identity:"
git config --global --get user.name || echo "(none)"
git config --global --get user.email || echo "(none)"

# --- GitHub Repo Access Verification ---
git_repo="SustainableDevelopmentReform/csdr-cloud-spatial"
git_origin="git@github.com:$git_repo.git"

# Check access to GitHub repository
echo "Running command git ls-remote $git_origin"
git ls-remote $git_origin

if ! git ls-remote $git_origin &>/dev/null; then
  echo "ERROR: Failed to access GitHub repository '$git_repo'" >&2
  echo "Check that the deploy key has read access to the repository." >&2
  exit 1
fi

echo "Access to GitHub repository verified."

# --- Git Remote and Pull ---
# Set the origin remote to the SSH URL
git remote set-url origin $git_origin

# Pull latest changes from current branch
git_branch="$(git rev-parse --abbrev-ref HEAD)"
echo "Pulling latest changes from branch '$git_branch'"
git pull origin $git_branch

# --- Command Execution ---
# If arguments are provided, execute them. Otherwise, do nothing.
if [ "$#" -eq 0 ]; then
  echo "No command provided. Entrypoint setup complete."
else
  exec "$@"
fi
