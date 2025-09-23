#!/bin/bash

# ABOUTME: Script to launch Claude Code using the local LiteLLM+Luthien proxy
# ABOUTME: Configures environment to route Claude API calls through local proxy

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🚀 Launching Claude Code with Luthien Proxy${NC}"

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}❌ .env file not found. Please run ./scripts/quick_start.sh first${NC}"
    exit 1
fi

# Source environment variables
set -a
source .env
set +a

# Check if proxy is running
PROXY_PORT="${LITELLM_PORT:-4000}"
CONTROL_PLANE_PORT="${CONTROL_PLANE_PORT:-8081}"

echo -e "${YELLOW}🔍 Checking proxy status...${NC}"

if ! curl -sf "http://localhost:${PROXY_PORT}/health/liveness" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Proxy not detected. Starting services...${NC}"
    ./scripts/quick_start.sh
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to start services${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}✅ Proxy is running on port ${PROXY_PORT}${NC}"

# Check for Anthropic API key
if [ -z "${ANTHROPIC_API_KEY}" ] || [ "${ANTHROPIC_API_KEY}" == "your_anthropic_api_key_here" ]; then
    echo -e "${RED}❌ ANTHROPIC_API_KEY not configured in .env${NC}"
    echo -e "${YELLOW}Please add your Anthropic API key to the .env file${NC}"
    exit 1
fi

# Export proxy configuration for Claude Code
export ANTHROPIC_BASE_URL="http://localhost:${PROXY_PORT}/"
export ANTHROPIC_API_KEY="${LITELLM_MASTER_KEY:-sk-luthien-dev-key}"
export ANTHROPIC_MODEL="claude-opus-4-1"
export ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-4"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-3-5-haiku"
export CLAUDE_CODE_SUBAGENT_MODEL="claude-sonnet-4"


echo -e "${BLUE}📋 Proxy Configuration:${NC}"
echo -e "   • Proxy URL:       ${ANTHROPIC_BASE_URL}"
echo -e "   • Proxy API Key:   ${ANTHROPIC_API_KEY:0:10}..."
echo -e "   • Control Plane:   http://localhost:${CONTROL_PLANE_PORT}"
echo ""
echo -e "${GREEN}🎯 Claude Code will now route through the Luthien proxy${NC}"
echo -e "${YELLOW}📊 Monitor requests at: http://localhost:${CONTROL_PLANE_PORT}/trace${NC}"
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
