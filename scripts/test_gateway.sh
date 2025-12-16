#!/bin/bash

# ABOUTME: Automated test script for gateway health and API compatibility
# ABOUTME: Tests OpenAI and Anthropic APIs with streaming and non-streaming modes

set -e

# Configuration
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
API_KEY="${PROXY_API_KEY:-sk-luthien-dev-key}"

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

# Wait for gateway to be ready (up to 30 seconds)
# Tested startup times: restart ~2s, full down/up 18-25s
echo "Waiting for gateway..."
dots=""
for i in {1..15}; do
    dots="${dots}."
    printf "\r%s Attempt %d/15" "$dots" "$i"
    if curl -sf "$GATEWAY_URL/health" > /dev/null 2>&1; then
        printf "\n${GREEN}Gateway ready!${NC}\n"
        break
    fi
    if [ $i -eq 15 ]; then
        printf "\n${RED}Gateway not ready after 30s${NC}\n"
        exit 1
    fi
    sleep 2
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

# Helper function to extract content from response
extract_content() {
    jq -r '.choices[0].message.content // empty'
}

# Helper function to validate content contains greeting
validate_greeting() {
    grep -iq "hello\|hi\|hey\|greetings"
}

# Helper function to validate streaming response
validate_streaming() {
    local content=$(cat)
    echo "$content" | grep -q "^data: {" && echo "$content" | grep -q "delta"
}

# Helper function to validate Anthropic response
validate_anthropic() {
    jq -e '.content[0].text | length > 0' > /dev/null
}

# Helper function to validate Anthropic streaming
validate_anthropic_streaming() {
    local content=$(cat)
    echo "$content" | grep -q "^data: {" && echo "$content" | grep -q "content_block_delta"
}

echo "=== Health Check ==="
test_endpoint "Health endpoint" \
    "curl -sf $GATEWAY_URL/health" \
    "jq -e '.status == \"healthy\"'"
echo ""

echo "=== OpenAI Chat Completions API ==="

# Test gpt-4o-mini non-streaming
test_endpoint "gpt-4o-mini (non-streaming)" \
    "curl -sf $GATEWAY_URL/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer $API_KEY' -d '{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in 2 words\"}],\"stream\":false,\"max_tokens\":20}'" \
    "jq -e '.choices[0].message.content | length > 0'"

# Test gpt-4o-mini streaming
test_endpoint "gpt-4o-mini (streaming)" \
    "curl -sf $GATEWAY_URL/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer $API_KEY' -d '{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"Count to 3\"}],\"stream\":true,\"max_tokens\":50}' | head -5" \
    "validate_streaming"

# Test gpt-5 non-streaming
test_endpoint "gpt-5 (non-streaming)" \
    "curl -sf $GATEWAY_URL/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer $API_KEY' -d '{\"model\":\"gpt-5\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in 2 words\"}],\"stream\":false,\"max_tokens\":100,\"verbosity\":\"low\"}'" \
    "jq -e '.id | length > 0'"

# Test claude-sonnet-4-5 via OpenAI API non-streaming
test_endpoint "claude-sonnet-4-5 via OpenAI API (non-streaming)" \
    "curl -sf $GATEWAY_URL/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer $API_KEY' -d '{\"model\":\"claude-sonnet-4-5\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in 2 words\"}],\"stream\":false,\"max_tokens\":20}'" \
    "jq -e '.choices[0].message.content | length > 0'"

# Test claude-sonnet-4-5 via OpenAI API streaming
test_endpoint "claude-sonnet-4-5 via OpenAI API (streaming)" \
    "curl -sf $GATEWAY_URL/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer $API_KEY' -d '{\"model\":\"claude-sonnet-4-5\",\"messages\":[{\"role\":\"user\",\"content\":\"Count to 3\"}],\"stream\":true,\"max_tokens\":50}' | head -5" \
    "validate_streaming"

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
    "curl -s $GATEWAY_URL/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer $API_KEY' -d '{\"model\":\"gpt-4o-mini\",\"stream\":false,\"max_tokens\":20}' -w '%{http_code}' -o /dev/null" \
    "grep -qE '^(4[0-9][0-9]|500)$'"

# Test invalid API key
test_endpoint "Invalid API key (should fail)" \
    "curl -s $GATEWAY_URL/v1/chat/completions -H 'Content-Type: application/json' -H 'Authorization: Bearer invalid-key' -d '{\"model\":\"gpt-4o-mini\",\"messages\":[{\"role\":\"user\",\"content\":\"test\"}],\"stream\":false,\"max_tokens\":20}' -w '%{http_code}' -o /dev/null" \
    "grep -q '^40[13]$'"

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
