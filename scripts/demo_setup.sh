#!/usr/bin/env bash
# ABOUTME: Set up the Luthien data-protection demo end-to-end on this machine.
# ABOUTME: Copies the template to ~/luthien-demo/, ensures the gateway is running,
# ABOUTME: and configures the demo policy chain. Safe to re-run.
#
# Usage: ./scripts/demo_setup.sh [mode]
#   mode: "block" (default), "fail", or "off" — see scripts/demo_toggle.sh
#
# Prereqs:
#   - .env with ADMIN_API_KEY (and ANTHROPIC_API_KEY if you want passthrough)
#   - uv installed
#   - Claude Desktop in developer mode pointed at http://localhost:8000

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
TEMPLATE_DIR="${REPO_ROOT}/dev/demo/template"
DEMO_DIR="${DEMO_DIR:-${HOME}/luthien-demo}"
PORT="${GATEWAY_PORT:-8000}"
MODE="${1:-block}"

echo "==> Luthien demo setup (mode=${MODE}, demo dir=${DEMO_DIR}, port=${PORT})"

if [[ ! -d "${TEMPLATE_DIR}" ]]; then
    echo "ERROR: template not found at ${TEMPLATE_DIR}" >&2
    exit 1
fi

# 1. Materialize the demo project dir from template
echo "==> Refreshing ${DEMO_DIR} from template"
rm -rf "${DEMO_DIR}"
mkdir -p "${DEMO_DIR}"
cp -R "${TEMPLATE_DIR}/." "${DEMO_DIR}/"
echo "    files:"
find "${DEMO_DIR}" -type f | sed 's|^|      |'

# 2. Ensure gateway is running on $PORT
if ! curl -sS -m 2 "http://localhost:${PORT}/ready" >/dev/null 2>&1; then
    echo "==> Gateway not responding on :${PORT}; starting it"
    mkdir -p .e2e-logs
    GATEWAY_PORT="${PORT}" nohup uv run python -m luthien_proxy.main --local \
        > .e2e-logs/gateway.log 2>&1 &
    echo "    pid=$!"
    for i in $(seq 1 20); do
        if curl -sS -m 1 "http://localhost:${PORT}/ready" >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
    if ! curl -sS -m 1 "http://localhost:${PORT}/ready" >/dev/null 2>&1; then
        echo "ERROR: gateway didn't come up; check .e2e-logs/gateway.log" >&2
        exit 1
    fi
fi
echo "==> Gateway ready on :${PORT}"

# 3. Set policy chain
"${REPO_ROOT}/scripts/demo_toggle.sh" "${MODE}"

echo
echo "==> Setup complete."
echo
echo "Demo project:      ${DEMO_DIR}"
echo "Gateway URL:       http://localhost:${PORT}"
echo "Activity monitor:  http://localhost:${PORT}/activity"
echo
echo "Next steps:"
echo "  1. In Claude Desktop -> developer mode, set the gateway to http://localhost:${PORT}"
echo "  2. Point Claude at ${DEMO_DIR} as the working directory"
echo "  3. Ask Claude to read team-feedback.md and act on the feedback"
echo
echo "Toggle modes during the demo:"
echo "  ./scripts/demo_toggle.sh block   # the safe demo — Luthien blocks the destructive call"
echo "  ./scripts/demo_toggle.sh fail    # the unsafe demo — destructive call reaches Claude (may delete files)"
echo "  ./scripts/demo_toggle.sh off     # pass-through, no policy in the loop"
echo
echo "Reset the demo project between runs:  ./scripts/demo_reset.sh"
