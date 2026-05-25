#!/usr/bin/env bash
# Entrypoint for the SAMI harness container.
# Starts the Luthien gateway in the background, waits for it to be healthy,
# then drops to an interactive shell so the user can run 'opencode'.
set -euo pipefail

GATEWAY_PORT="${GATEWAY_PORT:-8000}"
GATEWAY_URL="http://localhost:${GATEWAY_PORT}"

# ── Start gateway ─────────────────────────────────────────────────
echo "==> Starting Luthien gateway on port ${GATEWAY_PORT}..."
cd /app
uv run python -m luthien_proxy.main &
GATEWAY_PID=$!

# ── Graceful shutdown ─────────────────────────────────────────────
cleanup() {
    echo ""
    echo "==> Shutting down gateway (PID ${GATEWAY_PID})..."
    kill "${GATEWAY_PID}" 2>/dev/null || true
    wait "${GATEWAY_PID}" 2>/dev/null || true
    echo "==> Shutdown complete."
}
trap cleanup SIGTERM SIGINT

# ── Wait for health ───────────────────────────────────────────────
echo "==> Waiting for gateway to become healthy (up to 30s)..."
for i in $(seq 1 30); do
    if curl -fsS "${GATEWAY_URL}/health" > /dev/null 2>&1; then
        echo "==> Gateway ready at ${GATEWAY_URL}"
        break
    fi
    if [ "${i}" -eq 30 ]; then
        echo "ERROR: Gateway did not start within 30 seconds" >&2
        kill "${GATEWAY_PID}" 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

# ── Export proxy vars for opencode plugin ─────────────────────────
export LUTHIEN_PROXY_URL="${GATEWAY_URL}"
export ANTHROPIC_BASE_URL="${GATEWAY_URL}"

echo ""
echo "  Luthien gateway : ${LUTHIEN_PROXY_URL}"
echo "  opencode config : ${OPENCODE_CONFIG:-/root/.config/opencode/opencode.json}"
echo ""
echo "  Run 'opencode' to start a session."
echo ""

# Drop to an interactive login shell; gateway keeps running in background.
# The trap above ensures cleanup on shell exit.
exec /bin/bash -l
