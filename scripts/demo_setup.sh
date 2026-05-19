#!/usr/bin/env bash
# ABOUTME: Bootstrap a named Luthien demo end-to-end on this machine.
# ABOUTME: Materializes the demo workspace from its template, starts the
# ABOUTME: gateway, and sets the active policy. Safe to re-run.
#
# Usage: ./scripts/demo_setup.sh [demo] [state] [surface]
#   Defaults: demo=rm-rf, state=block, surface=claude-code.
#   List demos:  ./scripts/demo_setup.sh --list
#
# Prereqs:
#   - .env with ADMIN_API_KEY (and ANTHROPIC_API_KEY for passthrough)
#   - uv installed
#   - The client surface configured to talk to http://localhost:8000

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

if [[ "${1:-}" == "--list" ]]; then
    python3 scripts/_demo_manifest.py list
    exit 0
fi

DEMO="${1:-rm-rf}"
STATE="${2:-block}"
SURFACE="${3:-claude-code}"
PORT="${GATEWAY_PORT:-8000}"

DEMO_DIR=$(python3 scripts/_demo_manifest.py demo-dir "${DEMO}")
TEMPLATE_DIR=$(python3 scripts/_demo_manifest.py template-dir "${DEMO}")

echo "==> Luthien demo setup"
echo "    demo:       ${DEMO}"
echo "    state:      ${STATE}"
echo "    surface:    ${SURFACE}"
echo "    workspace:  ${DEMO_DIR}"
echo "    gateway:    http://localhost:${PORT}"

if [[ ! -d "${TEMPLATE_DIR}" ]]; then
    echo "ERROR: template not found at ${TEMPLATE_DIR}" >&2
    exit 1
fi

# 1. Materialize the demo workspace from template
echo "==> Refreshing ${DEMO_DIR} from template"
rm -rf "${DEMO_DIR}"
mkdir -p "$(dirname "${DEMO_DIR}")"
cp -R "${TEMPLATE_DIR}" "${DEMO_DIR}"
echo "    files:"
find "${DEMO_DIR}" -type f | sed 's|^|      |'

# 2. Ensure gateway is running on $PORT
if ! curl -sS -m 2 "http://localhost:${PORT}/ready" >/dev/null 2>&1; then
    echo "==> Gateway not responding on :${PORT}; starting it"
    mkdir -p .e2e-logs
    GATEWAY_PORT="${PORT}" nohup uv run python -m luthien_proxy.main --local \
        > .e2e-logs/gateway.log 2>&1 &
    echo "    pid=$!"
    # uv may need to install deps on first run; wait generously.
    for _ in $(seq 1 120); do
        if curl -sS -m 1 "http://localhost:${PORT}/ready" >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    if ! curl -sS -m 1 "http://localhost:${PORT}/ready" >/dev/null 2>&1; then
        echo "ERROR: gateway didn't come up; check .e2e-logs/gateway.log" >&2
        exit 1
    fi
fi
echo "==> Gateway ready on :${PORT}"

# 3. Set policy chain
"${REPO_ROOT}/scripts/demo_toggle.sh" "${DEMO}" "${STATE}" "${SURFACE}"

cat <<EOF

==> Setup complete.

Demo workspace:    ${DEMO_DIR}
Gateway URL:       http://localhost:${PORT}
Activity monitor:  http://localhost:${PORT}/activity

Demo-specific guidance lives in:
    dev/demo/${DEMO}/README.md

Switch state during the demo (keeping demo=${DEMO}, surface=${SURFACE}):
    ./scripts/demo_toggle.sh ${DEMO} block     ${SURFACE}   # Luthien blocks
    ./scripts/demo_toggle.sh ${DEMO} dontblock ${SURFACE}   # destructive call reaches client
    ./scripts/demo_toggle.sh ${DEMO} off                    # NoOp passthrough

Reset the workspace between rehearsals:
    ./scripts/demo_reset.sh ${DEMO}

EOF
