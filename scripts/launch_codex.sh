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

set -euo pipefail

CMD=${1:-codex}
shift || true

export OPENAI_API_URL=localhost:4000
export OPENAI_API_KEY=$LITELLM_MASTER_KEY

exec "$CMD" "$@"
