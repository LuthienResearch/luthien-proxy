#!/usr/bin/env bash

# ABOUTME: Minimal launcher for Codex through the V2 gateway with policy enforcement
# ABOUTME: Sets the base URL and API key to route through V2 gateway

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

V2_PORT=${V2_GATEWAY_PORT:-8000}

set -euo pipefail

shift || true

# Check if V2 gateway is running
if ! curl -sf "http://localhost:${V2_PORT}/health" > /dev/null 2>&1; then
    echo "⚠️  V2 gateway not detected. Starting observability stack..."
    ./scripts/observability.sh up -d

    # Wait for gateway to be healthy
    echo "⏳ Waiting for V2 gateway to be ready..."
    for i in {1..30}; do
        if curl -sf "http://localhost:${V2_PORT}/health" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    if ! curl -sf "http://localhost:${V2_PORT}/health" > /dev/null 2>&1; then
        echo "❌ V2 gateway failed to start"
        exit 1
    fi
fi

echo "✅ V2 gateway is running on port ${V2_PORT}"
echo "📊 Monitor at: http://localhost:${V2_PORT}/v2/activity/monitor"

# Use PROXY_API_KEY for V2 gateway authentication
export LITELLM_MASTER_KEY="${PROXY_API_KEY:-sk-luthien-dev-key}"

codex \
  -c model_providers.litellm.name=v2-gateway \
  -c model_providers.litellm.base_url=http://localhost:${V2_PORT}/v1 \
  -c model_providers.litellm.env_key=LITELLM_MASTER_KEY \
  -c model_providers.litellm.wire_api=chat \
  -c model_provider=litellm \
  -c model="gpt-5" \
  -c show_raw_agent_reasoning=true
