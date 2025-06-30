#!/bin/bash
set -euo pipefail

# Use the env var if already set, otherwise fetch from AWS Secrets Manager
if [[ -z "${GIT_DEPLOY_KEY_B64:-}" ]]; then
  echo "GIT_DEPLOY_KEY_B64 not provided — fetching from AWS Secrets Manager..."

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

# Set up SSH directory
mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Decode and write the deploy key
echo "$GIT_DEPLOY_KEY_B64" | base64 -d > /root/.ssh/id_rsa
chmod 600 /root/.ssh/id_rsa

# Add GitHub to known hosts
ssh-keyscan github.com >> /root/.ssh/known_hosts
chmod 644 /root/.ssh/known_hosts

# Configure Git user identity if provided
if [[ -n "${GIT_USER_NAME:-}" ]]; then
  git config --global user.name "$GIT_USER_NAME"
fi

if [[ -n "${GIT_USER_EMAIL:-}" ]]; then
  git config --global user.email "$GIT_USER_EMAIL"
fi

# Optional: echo git identity to verify
echo "Using Git identity:"
git config --global --get user.name || echo "(none)"
git config --global --get user.email || echo "(none)"

git_repo="SustainableDevelopmentReform/csdr-cloud-spatial"
git_origin="git@github.com:$git_repo.git"

echo "Running command git ls-remote $git_origin"
git ls-remote $git_origin

# ✅ Verify GitHub repo access
echo "Checking access to GitHub repository..."
if ! git ls-remote $git_origin &>/dev/null; then
  echo "❌ ERROR: Failed to access GitHub repository '$git_repo'" >&2
  echo "Check that the deploy key has read access to the repository." >&2
  exit 1
fi
echo "✅ Access to GitHub repository verified."

echo "Setting origin to $git_origin"
git remote set-url origin $git_origin

git_branch="$(git rev-parse --abbrev-ref HEAD)"
echo "Pulling latest changes from branch '$git_branch'"
git pull origin $git_branch

if [ "$#" -eq 0 ]; then
    dvc repro --all-pipelines

    if [[ "${AUTO_COMMIT_DVC:-false}" == "true" ]]; then
      echo "Attempting auto-commit of DVC pipeline results..."
      csdr dvc publish
    else
      echo "Skipping auto-commit of DVC pipeline results."
      csdr dvc publish --no-commit
    fi
else
    exec "$@"
fi
