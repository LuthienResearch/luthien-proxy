#!/usr/bin/env bash
# Track A Smoke Test: OpenCode + opencode-luthien plugin + gateway + mock providers
#
# Usage:
#   ./scripts/track_a_smoke.sh           # Happy path (A1-A5)
#   ./scripts/track_a_smoke.sh --a6-fallback  # A6 fallback test
#
# Prerequisites:
#   - OpenCode installed (opencode --version)
#   - opencode-luthien plugin built and installed to ~/.config/opencode/plugins/
#   - Gateway dependencies installed (uv sync)
#   - PROXY_API_KEY set in .env
#
# See dev-README.md "Track A Smoke Test" for full procedure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EVIDENCE_DIR="$REPO_ROOT/.sisyphus/evidence/track-a-17-smoke"

# Load .env
if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$REPO_ROOT/.env"
    set +a
fi

A6_FALLBACK="${1:-}"

echo "=== Track A Smoke Test ==="
echo "Evidence dir: $EVIDENCE_DIR"
mkdir -p "$EVIDENCE_DIR/happy-path" "$EVIDENCE_DIR/a6-fallback"

# Pick free ports
MOCK_ANTHROPIC_PORT=9000
MOCK_OPENAI_PORT=9001
MOCK_GEMINI_PORT=9002
GATEWAY_PORT=8000

echo "Starting mock servers..."
export MOCK_ANTHROPIC_PORT MOCK_OPENAI_PORT MOCK_GEMINI_PORT
export ANTHROPIC_BASE_URL="http://localhost:$MOCK_ANTHROPIC_PORT"
export OPENAI_BASE_URL="http://localhost:$MOCK_OPENAI_PORT"
export GEMINI_BASE_URL="http://localhost:$MOCK_GEMINI_PORT"

# Start mock servers
uv run python -m tests.luthien_proxy.e2e_tests.mock_anthropic.server --port "$MOCK_ANTHROPIC_PORT" &
MOCK_ANTHROPIC_PID=$!
uv run python -m tests.luthien_proxy.e2e_tests.mock_openai.server --port "$MOCK_OPENAI_PORT" &
MOCK_OPENAI_PID=$!
uv run python -m tests.luthien_proxy.e2e_tests.mock_gemini.server --port "$MOCK_GEMINI_PORT" &
MOCK_GEMINI_PID=$!

sleep 2

echo "Starting gateway on port $GATEWAY_PORT..."
uv run python -m luthien_proxy.main --gateway-port "$GATEWAY_PORT" &
GATEWAY_PID=$!
sleep 3

cleanup() {
    echo "Cleaning up..."
    kill "$GATEWAY_PID" "$MOCK_ANTHROPIC_PID" "$MOCK_OPENAI_PID" "$MOCK_GEMINI_PID" 2>/dev/null || true
}
trap cleanup EXIT

export LUTHIEN_PROXY_URL="http://localhost:$GATEWAY_PORT"

if [ "$A6_FALLBACK" = "--a6-fallback" ]; then
    echo "=== A6 Fallback Test ==="
    echo "Stopping gateway..."
    kill "$GATEWAY_PID" 2>/dev/null || true
    sleep 1
    echo "Running OpenAI chat with gateway down..."
    opencode run --model openai/gpt-4o "say hi" \
        2>"$EVIDENCE_DIR/a6-fallback/opencode-stderr.log" \
        >"$EVIDENCE_DIR/a6-fallback/opencode-stdout.log" || true
    echo "A6 fallback test complete. Check evidence:"
    echo "  $EVIDENCE_DIR/a6-fallback/"
else
    echo "=== Happy Path Test ==="
    echo "Running Anthropic chat..."
    opencode run --model anthropic/claude-3-5-sonnet-20241022 "say hi" \
        2>"$EVIDENCE_DIR/happy-path/anthropic-session.log" || true
    echo "Running OpenAI chat..."
    opencode run --model openai/gpt-4o "say hi" \
        2>"$EVIDENCE_DIR/happy-path/openai-session.log" || true
    echo "Running Gemini chat..."
    opencode run --model google/gemini-1.5-flash "say hi" \
        2>"$EVIDENCE_DIR/happy-path/gemini-session.log" || true

    echo "Querying request_logs..."
    sqlite3 ~/.luthien/local.db \
        "SELECT session_id, agent, model, endpoint FROM request_logs WHERE session_id IS NOT NULL ORDER BY created_at DESC LIMIT 10;" \
        > "$EVIDENCE_DIR/happy-path/request-logs-query.txt" 2>&1 || true

    echo "Happy path test complete. Check evidence:"
    echo "  $EVIDENCE_DIR/happy-path/"
fi
