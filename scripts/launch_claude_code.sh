#!/bin/bash
# Requires: bash 3.2+
# ABOUTME: Script to launch Claude Code using the gateway with policy enforcement
# ABOUTME: Configures environment to route Claude API calls through gateway

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=auth_mode_check.sh
source "${SCRIPT_DIR}/auth_mode_check.sh"

echo -e "${BLUE}🚀 Launching Claude Code with Gateway${NC}"

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}❌ .env file not found. Please run ./scripts/quick_start.sh first${NC}"
    exit 1
fi

# Source only the variables we need from .env
if [ -f .env ]; then
    PROXY_API_KEY="$(grep -E '^PROXY_API_KEY=' .env | cut -d '=' -f2-)"
    export PROXY_API_KEY
    GATEWAY_PORT="$(grep -E '^GATEWAY_PORT=' .env | cut -d '=' -f2-)"
    export GATEWAY_PORT
    GATEWAY_HOST="$(grep -E '^GATEWAY_HOST=' .env | cut -d '=' -f2-)"
    export GATEWAY_HOST
fi

# Check if gateway is running
GATEWAY_PORT_VAR="${GATEWAY_PORT:-8000}"

echo -e "${YELLOW}🔍 Checking gateway status...${NC}"

if ! curl -sf "http://localhost:${GATEWAY_PORT_VAR}/health" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Gateway not detected. Starting gateway...${NC}"
    ./scripts/start_gateway.sh &

    # Wait for gateway to be healthy
    echo -e "${YELLOW}⏳ Waiting for gateway to be ready...${NC}"
    for _i in {1..30}; do
        if curl -sf "http://localhost:${GATEWAY_PORT_VAR}/health" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    if ! curl -sf "http://localhost:${GATEWAY_PORT_VAR}/health" > /dev/null 2>&1; then
        echo -e "${RED}❌ gateway failed to start${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}✅ gateway is running on port ${GATEWAY_PORT_VAR}${NC}"

# Prepare gateway configuration for Claude Code
# Note: Don't include /v1 in base URL - Anthropic SDK adds it automatically
# Claude Code requires these to be set inline when launching to skip onboarding
PROXY_KEY="${PROXY_API_KEY:-sk-luthien-dev-key}"
GATEWAY_URL="http://localhost:${GATEWAY_PORT_VAR}/"

# Detect auth mode from /health endpoint:
#   proxy_key — server always uses its own Anthropic API key; requests are billed to it.
#   anything else — OAuth passthrough (Claude Pro/Max subscribers, no per-token charges).
HEALTH_RESPONSE=$(curl -sf "http://localhost:${GATEWAY_PORT_VAR}/health")
AUTH_MODE=$(echo "$HEALTH_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('auth_mode',''))" 2>/dev/null)

if [[ "${AUTH_MODE}" == "passthrough" ]]; then
    AUTH_MODE_LABEL="Claude Max / OAuth passthrough"
    USE_OAUTH=true
elif [[ "${AUTH_MODE}" == "both" ]]; then
    # In both mode, the gateway handles credential selection server-side.
    # Default to OAuth; the gateway falls back to the proxy key if needed.
    AUTH_MODE_LABEL="OAuth passthrough or API key fallback (both mode)"
    USE_OAUTH=true
else
    # proxy_key: server always uses its API key.
    AUTH_MODE_LABEL="API key (proxy_key mode)"
    USE_OAUTH=false
fi

echo -e "${BLUE}📋 Gateway Configuration:${NC}"
echo -e "   • Gateway URL:     ${GATEWAY_URL} (SDK will append /v1/messages)"
echo -e "   • Auth mode:       ${AUTH_MODE_LABEL}"
echo ""

# Interactive auth mode nudge — offer to switch away from proxy_key
if new_mode=$(check_auth_mode_interactive "$AUTH_MODE"); then
    update_auth_mode_env "$new_mode"

    admin_key=$(grep -E '^ADMIN_API_KEY=' .env 2>/dev/null | cut -d '=' -f2-)
    update_auth_mode_api "$new_mode" "http://localhost:${GATEWAY_PORT_VAR}" "$admin_key"

    AUTH_MODE="$new_mode"
    echo ""

    # Re-resolve OAuth preference after mode change
    if [[ "${AUTH_MODE}" == "passthrough" ]]; then
        AUTH_MODE_LABEL="Claude Max / OAuth passthrough"
        USE_OAUTH=true
    elif [[ "${AUTH_MODE}" == "both" ]]; then
        AUTH_MODE_LABEL="OAuth passthrough or API key fallback (both mode)"
        USE_OAUTH=true
    fi

    echo -e "${BLUE}📋 Updated configuration:${NC}"
    echo -e "   • Auth mode:       ${AUTH_MODE_LABEL}"
    echo ""
fi

echo -e "${GREEN}🎯 Claude Code will now route through the gateway with policy enforcement${NC}"
echo -e "${YELLOW}📊 Monitor requests at:${NC}"
echo -e "   • Activity Monitor:  http://localhost:${GATEWAY_PORT_VAR}/activity/monitor"
echo -e "   • Diff Viewer:       http://localhost:${GATEWAY_PORT_VAR}/diffs"
echo ""

# Launch Claude Code
echo -e "${BLUE}🤖 Starting Claude Code...${NC}"
echo ""

# Check if claude command exists
if ! command -v claude &> /dev/null; then
    echo -e "${RED}❌ Claude Code CLI not found${NC}"
    echo -e "${YELLOW}Install it with: npm install -g @anthropic-ai/claude-cli${NC}"
    exit 1
fi

# Launch Claude Code with proxy configuration.
# Setting env vars inline ensures Claude Code picks them up at startup.
#
# API key mode: override ANTHROPIC_API_KEY with the proxy's own key so Claude Code
#   authenticates to the proxy (not directly to Anthropic). The proxy uses the real
#   ANTHROPIC_API_KEY from .env for the upstream call.
#
# OAuth mode: only set ANTHROPIC_BASE_URL. Claude Code uses its existing OAuth session
#   and sends bearer tokens to the proxy, which forwards them to Anthropic.
#   No server-side API key is needed.

# You can add these env vars to change the default models used by Claude Code:
# ANTHROPIC_MODEL="anthropic/claude-sonnet-4-5"
# ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-4-5"
# ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-3-5-haiku"
# CLAUDE_CODE_SUBAGENT_MODEL="anthropic/claude-sonnet-4-5"
# See https://docs.claude.com/en/docs/claude-code/settings#environment-variables
if [ "${USE_OAUTH}" = false ]; then
    env \
      ANTHROPIC_BASE_URL="${GATEWAY_URL}" \
      ANTHROPIC_API_KEY="${PROXY_KEY}" \
      claude "$@"
else
    # Unset ANTHROPIC_API_KEY to avoid Claude Code's "both token and API key set" conflict warning.
    # In OAuth/both mode the proxy handles credential selection server-side; the key is not needed client-side.
    env -u ANTHROPIC_API_KEY \
      ANTHROPIC_BASE_URL="${GATEWAY_URL}" \
      claude "$@"
fi
