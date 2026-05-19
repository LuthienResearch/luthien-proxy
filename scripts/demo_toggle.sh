#!/usr/bin/env bash
# ABOUTME: Toggle the running gateway's policy for a named demo.
# ABOUTME: Reads the demo's manifest at dev/demo/<demo>/demo.toml.
#
# Usage:
#   ./scripts/demo_toggle.sh [demo] [state] [surface]
#
# Defaults: demo=rm-rf, state=block, surface=claude-code.
#
# State semantics (derived by the harness from each demo's manifest):
#   block      — MultiSerialPolicy([fabricator, protector]). Luthien blocks.
#   dontblock  — fabricator alone. The fabricated bad action reaches the client.
#   off        — NoOpPolicy. Pass-through, no demo behavior.
#
# Surface determines which client-side tool name the fabricator claims to be
# producing. Must be one of the surfaces the demo declares.
#
# List available demos:  ./scripts/demo_toggle.sh --list

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--list" ]]; then
    python3 scripts/_demo_manifest.py list
    exit 0
fi

DEMO="${1:-rm-rf}"
STATE="${2:-block}"
SURFACE="${3:-claude-code}"
PORT="${GATEWAY_PORT:-8000}"

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found; cannot read ADMIN_API_KEY" >&2
    exit 1
fi

AK=$(grep -E "^ADMIN_API_KEY=" .env | head -1 | cut -d= -f2- | sed 's/^"//; s/"$//')
if [[ -z "${AK}" ]]; then
    echo "ERROR: ADMIN_API_KEY not set in .env" >&2
    exit 1
fi

payload=$(python3 scripts/_demo_manifest.py policy "${DEMO}" "${STATE}" "${SURFACE}")

if [[ "${STATE}" == "off" ]]; then
    echo "==> demo=${DEMO} state=off (NoOpPolicy)"
else
    echo "==> demo=${DEMO} state=${STATE} surface=${SURFACE}"
fi

response=$(curl -sS -X POST \
    -H "Authorization: Bearer ${AK}" \
    -H "Content-Type: application/json" \
    -d "${payload}" \
    "http://localhost:${PORT}/api/admin/policy/set")

if ! echo "${response}" | python3 -c "import sys,json; sys.exit(0 if json.load(sys.stdin).get('success') else 1)" 2>/dev/null; then
    echo "ERROR: policy set failed" >&2
    echo "${response}" >&2
    exit 1
fi
echo "    OK"
