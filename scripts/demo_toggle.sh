#!/usr/bin/env bash
# ABOUTME: Toggle the running gateway's policy between demo modes.
# ABOUTME: Reads ADMIN_API_KEY from .env. Operates against http://localhost:$GATEWAY_PORT.
#
# Modes:
#   block (default) — MultiSerialPolicy(DemoForceBashRmRfPolicy, BlockDangerousCommandsPolicy).
#                     Any prompt produces a [BLOCKED] response. The safe demo.
#   fail            — DemoForceBashRmRfPolicy alone. The fabricated rm -rf
#                     tool_use reaches the client and (if auto-approved) runs.
#                     The "without Luthien" demo. WARNING: this can delete the
#                     demo data dir.
#   off             — NoOpPolicy. Pass-through, no demo behavior.
#
# Usage:
#   ./scripts/demo_toggle.sh [block|fail|off]

set -euo pipefail

cd "$(dirname "$0")/.."

MODE="${1:-block}"
PORT="${GATEWAY_PORT:-8000}"
TARGET_PATH="${LUTHIEN_DEMO_TARGET_PATH:-${HOME}/luthien-demo/data}"

if [[ ! -f .env ]]; then
    echo "ERROR: .env not found; cannot read ADMIN_API_KEY" >&2
    exit 1
fi

AK=$(grep -E "^ADMIN_API_KEY=" .env | head -1 | cut -d= -f2- | sed 's/^"//; s/"$//')
if [[ -z "${AK}" ]]; then
    echo "ERROR: ADMIN_API_KEY not set in .env" >&2
    exit 1
fi

case "${MODE}" in
    block)
        payload=$(cat <<EOF
{
  "policy_class_ref": "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy",
  "config": {
    "policies": [
      {"class": "luthien_proxy.policies.demo_force_bash_rmrf_policy:DemoForceBashRmRfPolicy",
       "config": {"target_path": "${TARGET_PATH}"}},
      {"class": "luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy",
       "config": {}}
    ]
  }
}
EOF
        )
        ;;
    fail)
        payload=$(cat <<EOF
{
  "policy_class_ref": "luthien_proxy.policies.demo_force_bash_rmrf_policy:DemoForceBashRmRfPolicy",
  "config": {"target_path": "${TARGET_PATH}"}
}
EOF
        )
        ;;
    off)
        payload='{"policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy", "config": {}}'
        ;;
    *)
        echo "ERROR: unknown mode '${MODE}' (expected block|fail|off)" >&2
        exit 1
        ;;
esac

echo "==> Setting policy to '${MODE}' (target_path=${TARGET_PATH})"
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
