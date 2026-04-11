#!/bin/bash
# Requires: bash 3.2+
# ABOUTME: Automated test script for gateway health and API compatibility
# ABOUTME: Tests Anthropic API with streaming and non-streaming modes

set -e

# Configuration
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
API_KEY="${CLIENT_API_KEY:-sk-luthien-dev-key}"
# Real Anthropic key for passthrough auth test (optional, from .env)
ANTHROPIC_KEY="${ANTHROPIC_API_KEY:-}"

# Retry settings for gateway startup wait
# Values determined by timing tests: restart ~2s, full down/up 18-25s
# See: https://github.com/LuthienResearch/luthien-proxy/pull/111
GATEWAY_RETRY_COUNT=15      # Number of health check attempts
GATEWAY_RETRY_INTERVAL=2    # Seconds between attempts (total wait: 15 × 2 = 30s)

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counters
TESTS_PASSED=0
TESTS_FAILED=0

echo "🧪 Testing Gateway at $GATEWAY_URL"
echo ""

# Wait for gateway to be ready
# Why: After quick_start.sh, gateway needs 18-25s to fully start.
#      Without this, users running test_gateway.sh immediately would see failures.
echo "Waiting for gateway..."
dots=""
for i in $(seq 1 $GATEWAY_RETRY_COUNT); do
    dots="${dots}."                                                              # Accumulate dots for visual progress
    printf "\r%s Attempt %d/%d" "$dots" "$i" "$GATEWAY_RETRY_COUNT"              # \r returns cursor to line start, overwrites previous
    if curl -sf "$GATEWAY_URL/health" > /dev/null 2>&1; then                     # -s silent, -f fail on HTTP errors
        # shellcheck disable=SC2059 # Color vars contain escape sequences that must be in the format string
        printf "\n${GREEN}Gateway ready!${NC}\n"
        break
    fi
    if [ "$i" -eq "$GATEWAY_RETRY_COUNT" ]; then                                 # On final attempt, fail instead of sleeping again
        total_wait=$((GATEWAY_RETRY_COUNT * GATEWAY_RETRY_INTERVAL))
        # shellcheck disable=SC2059
        printf "\n${RED}Gateway not ready after ${total_wait}s${NC}\n"
        exit 1
    fi
    sleep "$GATEWAY_RETRY_INTERVAL"
done

# Helper function to test endpoint
test_endpoint() {
    local test_name="$1"
    local curl_cmd="$2"
    local validation_cmd="$3"

    echo -n "Testing: $test_name... "

    if eval "$curl_cmd" | eval "$validation_cmd" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ PASS${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

# Helper functions below are invoked indirectly via eval in test_endpoint()
# shellcheck disable=SC2317,SC2329
validate_anthropic() {
    jq -e '.content[0].text | length > 0' > /dev/null
}

# shellcheck disable=SC2317,SC2329
validate_anthropic_streaming() {
    local content
    content="$(cat)"
    echo "$content" | grep -q "^data: {" && echo "$content" | grep -q "content_block_delta"
}

echo "=== Health Check ==="
test_endpoint "Health endpoint" \
    "curl -sf $GATEWAY_URL/health" \
    "jq -e '.status == \"healthy\"'"
echo ""

echo "=== Anthropic Messages API ==="

# Test claude-sonnet-4-5 non-streaming
test_endpoint "claude-sonnet-4-5 (non-streaming)" \
    "curl -sf $GATEWAY_URL/v1/messages -H 'Content-Type: application/json' -H 'Authorization: Bearer $API_KEY' -H 'anthropic-version: 2023-06-01' -d '{\"model\":\"claude-sonnet-4-5\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in 2 words\"}],\"max_tokens\":20}'" \
    "validate_anthropic"

# Test claude-sonnet-4-5 streaming
test_endpoint "claude-sonnet-4-5 (streaming)" \
    "curl -sf $GATEWAY_URL/v1/messages -H 'Content-Type: application/json' -H 'Authorization: Bearer $API_KEY' -H 'anthropic-version: 2023-06-01' -d '{\"model\":\"claude-sonnet-4-5\",\"messages\":[{\"role\":\"user\",\"content\":\"Count to 3\"}],\"max_tokens\":50,\"stream\":true}' | head -10" \
    "validate_anthropic_streaming"

echo ""
echo "=== Error Handling ==="

# Test missing messages field
test_endpoint "Missing messages field (should fail)" \
    "curl -s $GATEWAY_URL/v1/messages -H 'Content-Type: application/json' -H 'Authorization: Bearer $API_KEY' -H 'anthropic-version: 2023-06-01' -d '{\"model\":\"claude-sonnet-4-5\",\"max_tokens\":20}' -w '%{http_code}' -o /dev/null" \
    "grep -qE '^(4[0-9][0-9]|500)$'"

# Test invalid API key
test_endpoint "Invalid API key (should fail)" \
    "curl -s $GATEWAY_URL/v1/messages -H 'Content-Type: application/json' -H 'Authorization: Bearer invalid-key' -H 'anthropic-version: 2023-06-01' -d '{\"model\":\"claude-sonnet-4-5\",\"messages\":[{\"role\":\"user\",\"content\":\"test\"}],\"max_tokens\":20}' -w '%{http_code}' -o /dev/null" \
    "grep -q '^40[13]$'"

echo ""
echo "=== Bearer Token Auth (PR #222 regression test) ==="

# Verify gateway is in BOTH mode (not client_key) by checking the error message
# for a fake Bearer token. BOTH mode says "Invalid API key or credential";
# client_key mode says "Invalid API key". This is the exact bug from PR #222.
test_endpoint "Auth mode is BOTH (not client_key)" \
    "curl -s $GATEWAY_URL/v1/messages -H 'Content-Type: application/json' -H 'Authorization: Bearer fake-token-to-test-auth-mode' -H 'anthropic-version: 2023-06-01' -d '{\"model\":\"claude-sonnet-4-5\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":10}'" \
    "jq -e '.detail == \"Invalid API key or credential\"'"

# If ANTHROPIC_API_KEY is available, test actual passthrough with a real credential
# that isn't the proxy key. This validates the passthrough auth path end-to-end.
# Note: API keys must be sent via x-api-key (Bearer is for OAuth tokens only).
# This test is advisory — expired keys shouldn't fail the whole suite.
if [ -n "$ANTHROPIC_KEY" ]; then
    echo -n "Testing: Passthrough auth (real Anthropic key, not proxy key)... "
    if curl -sf "$GATEWAY_URL/v1/messages" -H 'Content-Type: application/json' -H "x-api-key: $ANTHROPIC_KEY" -H 'anthropic-version: 2023-06-01' -d '{"model":"claude-sonnet-4-5","messages":[{"role":"user","content":"Say hi in 2 words"}],"max_tokens":20}' | validate_anthropic > /dev/null 2>&1; then
        echo -e "${GREEN}✓ PASS${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo -e "${YELLOW}⚠ SKIP (key may be expired or invalid)${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Skipping passthrough auth test (ANTHROPIC_API_KEY not set)${NC}"
fi

echo ""
echo "==================================="
echo "Test Results:"
echo -e "  ${GREEN}Passed: $TESTS_PASSED${NC}"
echo -e "  ${RED}Failed: $TESTS_FAILED${NC}"
echo "==================================="

if [ $TESTS_FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed! 🎉${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
