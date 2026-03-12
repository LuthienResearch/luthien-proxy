#!/bin/bash
# Requires: bash 3.2+
# ABOUTME: Start the V2 integrated gateway (FastAPI + LiteLLM with control plane)
# ABOUTME: Runs on port 8000 (configurable via GATEWAY_PORT env var)

set -e

# Load environment variables from .env if it exists
if [ -f .env ]; then
    echo "Loading environment from .env..."
    export $(grep -v '^#' .env | xargs)
fi

# Set defaults
PORT=${GATEWAY_PORT:-8000}
API_KEY=${PROXY_API_KEY:-sk-luthien-dev-key}

# Validate required API keys
if [ -z "$OPENAI_API_KEY" ] || [ "$OPENAI_API_KEY" = "your_openai_api_key_here" ]; then
    echo "WARNING: OPENAI_API_KEY not set or using placeholder value"
fi

if [ -z "$ANTHROPIC_API_KEY" ] || [ "$ANTHROPIC_API_KEY" = "your_anthropic_api_key_here" ]; then
    echo "WARNING: ANTHROPIC_API_KEY not set or using placeholder value"
fi

echo "Starting V2 Gateway..."
echo "  Port: $PORT"
echo "  API Key: ${API_KEY:0:12}..."

# Export PROXY_API_KEY for the V2 gateway
export PROXY_API_KEY=$API_KEY

# Set PYTHONPATH to find luthien_proxy module
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)/src"

# Start the gateway
cd "$(dirname "$0")/.." && exec uv run python -m luthien_proxy.main
