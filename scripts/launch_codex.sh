#!/usr/bin/env bash

# ABOUTME: Minimal launcher for Codex through the gateway with policy enforcement
# ABOUTME: Sets the base URL and API key to route through gateway

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

GATEWAY_PORT_VAR=${GATEWAY_PORT:-8000}

set -euo pipefail

shift || true

# Check if gateway is running
if ! curl -sf "http://localhost:${GATEWAY_PORT_VAR}/health" > /dev/null 2>&1; then
    echo "⚠️  gateway not detected. Starting observability stack..."
    ./scripts/observability.sh up -d

    # Wait for gateway to be healthy
    echo "⏳ Waiting for gateway to be ready..."
    for i in {1..30}; do
        if curl -sf "http://localhost:${GATEWAY_PORT_VAR}/health" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    if ! curl -sf "http://localhost:${GATEWAY_PORT_VAR}/health" > /dev/null 2>&1; then
        echo "❌ gateway failed to start"
        exit 1
    fi
fi

echo "✅ Gateway is running on port ${GATEWAY_PORT_VAR}"
echo "📊 Monitor at: http://localhost:${GATEWAY_PORT_VAR}/activity/monitor"

# Use PROXY_API_KEY for gateway authentication
export LITELLM_MASTER_KEY="${PROXY_API_KEY:-sk-luthien-dev-key}"

codex \
  -c model_providers.litellm.name=gateway \
  -c model_providers.litellm.base_url=http://localhost:${GATEWAY_PORT_VAR}/v1 \
  -c model_providers.litellm.env_key=LITELLM_MASTER_KEY \
  -c model_providers.litellm.wire_api=chat \
  -c model_provider=litellm \
  -c model="gpt-5" \
  -c show_raw_agent_reasoning=true
