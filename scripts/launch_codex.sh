#!/usr/bin/env bash

# Minimal launcher for Codex through the LiteLLM proxy
# Only sets the base URL and API key if needed.

set -euo pipefail

CMD=${1:-codex}
shift || true

# Determine proxy base URL
PROXY_URL="${OPENAI_BASE_URL:-${OPENAI_API_BASE:-${LITELLM_URL:-}}}"
if [[ -z "${PROXY_URL}" ]]; then
  PORT="${LITELLM_PORT:-4000}"
  PROXY_URL="http://localhost:${PORT}"
fi
if [[ "${PROXY_URL}" != http*://* ]]; then
  PROXY_URL="http://${PROXY_URL}"
fi

# Prefer existing OPENAI_API_KEY; otherwise use LITELLM_MASTER_KEY if present
if [[ -z "${OPENAI_API_KEY:-}" && -n "${LITELLM_MASTER_KEY:-}" ]]; then
  export OPENAI_API_KEY="${LITELLM_MASTER_KEY}"
fi

# Set base URL vars used by OpenAI-compatible tools
export OPENAI_BASE_URL="${PROXY_URL}"
export OPENAI_API_BASE="${PROXY_URL}"

echo "Using proxy: ${PROXY_URL}"

if ! command -v "$CMD" >/dev/null 2>&1; then
  echo "Command '$CMD' not found. Usage: scripts/launch_codex.sh codex [args]" >&2
  exit 1
fi

exec "$CMD" "$@"
