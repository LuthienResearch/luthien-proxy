#!/usr/bin/env bash

# Minimal launcher for Codex through the LiteLLM proxy
# Only sets the base URL and API key if needed.

# source .env, recursing up until found
CURDIR=$(pwd)
while [ ! -f $CURDIR/.env ] && [ "$CURDIR" != "/" ]; do
  CURDIR=$(dirname "$CURDIR")
done
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
else
  echo "No .env file found"
  exit 1
fi

LITELLM_PORT=${LITELLM_PORT:-4000}


set -euo pipefail

shift || true

env|grep OPENAI

codex \
  -c model_providers.litellm.name=litellm \
  -c model_providers.litellm.base_url=${LITELLM_BASE_URL:-http://localhost:${LITELLM_PORT}} \
  -c model_providers.litellm.env_key=LITELLM_MASTER_KEY \
  -c model_providers.litellm.wire_api=chat \
  -c model_provider=litellm \
  -c model="gpt-5"
