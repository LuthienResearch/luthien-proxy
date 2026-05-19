#!/usr/bin/env bash
# ABOUTME: Wipe and re-clone a named demo's workspace from its template.
# ABOUTME: Use between rehearsals (especially after running in `dontblock` state).
#
# Usage: ./scripts/demo_reset.sh [demo]
#   Default demo: rm-rf
#
# The workspace path comes from the demo's manifest. It's validated to
# contain `luthien-demo` and not be `/`, $HOME, or empty — so a broken
# manifest can't `rm -rf` the wrong directory.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--list" ]]; then
    python3 scripts/_demo_manifest.py list
    exit 0
fi

DEMO="${1:-rm-rf}"
TEMPLATE_DIR=$(python3 scripts/_demo_manifest.py template-dir "${DEMO}")
DEMO_DIR=$(python3 scripts/_demo_manifest.py demo-dir "${DEMO}")

if [[ ! -d "${TEMPLATE_DIR}" ]]; then
    echo "ERROR: template not found at ${TEMPLATE_DIR}" >&2
    exit 1
fi

echo "==> Resetting ${DEMO_DIR} from ${TEMPLATE_DIR}"
rm -rf "${DEMO_DIR}"
mkdir -p "$(dirname "${DEMO_DIR}")"
cp -R "${TEMPLATE_DIR}" "${DEMO_DIR}"

echo "==> Done. Files:"
find "${DEMO_DIR}" -type f | sed 's|^|    |'
