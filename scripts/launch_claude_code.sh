#!/bin/bash

# ABOUTME: Script to launch Claude Code using the V2 gateway with policy enforcement
# ABOUTME: Configures environment to route Claude API calls through V2 gateway

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Launching Claude Code with V2 Gateway${NC}"

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}❌ .env file not found. Please run ./scripts/quick_start.sh first${NC}"
    exit 1
fi

# Source environment variables
set -a
source .env
set +a

# Check if V2 gateway is running
V2_PORT="${V2_GATEWAY_PORT:-8000}"

echo -e "${YELLOW}🔍 Checking V2 gateway status...${NC}"

if ! curl -sf "http://localhost:${V2_PORT}/health" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  V2 gateway not detected. Starting observability stack...${NC}"
    ./scripts/observability.sh up -d

    # Wait for gateway to be healthy
    echo -e "${YELLOW}⏳ Waiting for V2 gateway to be ready...${NC}"
    for i in {1..30}; do
        if curl -sf "http://localhost:${V2_PORT}/health" > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    if ! curl -sf "http://localhost:${V2_PORT}/health" > /dev/null 2>&1; then
        echo -e "${RED}❌ V2 gateway failed to start${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}✅ V2 gateway is running on port ${V2_PORT}${NC}"

# Check for Anthropic API key
if [ -z "${ANTHROPIC_API_KEY}" ] || [ "${ANTHROPIC_API_KEY}" == "your_anthropic_api_key_here" ]; then
    echo -e "${RED}❌ ANTHROPIC_API_KEY not configured in .env${NC}"
    echo -e "${YELLOW}Please add your Anthropic API key to the .env file${NC}"
    exit 1
fi

# Export V2 gateway configuration for Claude Code
export ANTHROPIC_BASE_URL="http://localhost:${V2_PORT}/v1/"
export ANTHROPIC_API_KEY="${PROXY_API_KEY:-sk-luthien-dev-key}"
export ANTHROPIC_MODEL="anthropic/claude-sonnet-4-5"
export ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-4-5"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-3-5-haiku"
export CLAUDE_CODE_SUBAGENT_MODEL="anthropic/claude-sonnet-4-5"


echo -e "${BLUE}📋 V2 Gateway Configuration:${NC}"
echo -e "   • Gateway URL:     ${ANTHROPIC_BASE_URL}"
echo -e "   • API Key:         ${ANTHROPIC_API_KEY:0:10}..."
echo ""
echo -e "${GREEN}🎯 Claude Code will now route through the V2 gateway with policy enforcement${NC}"
echo -e "${YELLOW}📊 Monitor requests at:${NC}"
echo -e "   • Activity Monitor:  http://localhost:${V2_PORT}/v2/activity/monitor"
echo -e "   • Diff Viewer:       http://localhost:${V2_PORT}/v2/debug/diff"
echo -e "   • Grafana:           http://localhost:3000"
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
claude "$@"
