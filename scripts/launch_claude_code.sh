#!/bin/bash

# ABOUTME: Script to launch Claude Code using the gateway with policy enforcement
# ABOUTME: Configures environment to route Claude API calls through gateway

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Launching Claude Code with Gateway${NC}"

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}❌ .env file not found. Please run ./scripts/quick_start.sh first${NC}"
    exit 1
fi

# Source only the variables we need from .env
if [ -f .env ]; then
    export PROXY_API_KEY=$(grep -E '^PROXY_API_KEY=' .env | cut -d '=' -f2-)
    export GATEWAY_PORT=$(grep -E '^GATEWAY_PORT=' .env | cut -d '=' -f2-)
    export GATEWAY_HOST=$(grep -E '^GATEWAY_HOST=' .env | cut -d '=' -f2-)
fi

# Check if gateway is running
GATEWAY_PORT_VAR="${GATEWAY_PORT:-8000}"

echo -e "${YELLOW}🔍 Checking gateway status...${NC}"

if ! curl -sf "http://localhost:${GATEWAY_PORT_VAR}/health" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  gateway not detected. Starting observability stack...${NC}"
    ./scripts/observability.sh up -d

    # Wait for gateway to be healthy
    echo -e "${YELLOW}⏳ Waiting for gateway to be ready...${NC}"
    for i in {1..30}; do
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

echo -e "${BLUE}📋 Gateway Configuration:${NC}"
echo -e "   • Gateway URL:     ${GATEWAY_URL} (SDK will append /v1/messages)"
echo -e "   • API Key:         ${PROXY_KEY:0:10}... (sent as x-api-key header via ANTHROPIC_API_KEY)"
echo ""
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

# Launch Claude Code with proxy configuration
# Setting env vars inline ensures Claude Code picks them up at startup and skips onboarding
#
# ANTHROPIC_API_KEY (not ANTHROPIC_AUTH_TOKEN) is the reliable env var:
# - Claude Code sends it as `x-api-key` header, which the gateway expects
# - ANTHROPIC_AUTH_TOKEN works for some versions but behavior varies
# - The e2e tests also use ANTHROPIC_API_KEY for consistency
env \
  ANTHROPIC_BASE_URL="${GATEWAY_URL}" \
  ANTHROPIC_API_KEY="${PROXY_KEY}" \
  claude "$@"
  # You can add these arguments to modify the default models used
  # See https://docs.claude.com/en/docs/claude-code/settings#environment-variables
  # ANTHROPIC_MODEL="anthropic/claude-sonnet-4-5" \
  # ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-4-5" \
  # ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-3-5-haiku" \
  # CLAUDE_CODE_SUBAGENT_MODEL="anthropic/claude-sonnet-4-5" \
