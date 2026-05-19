#!/usr/bin/env bash
# ABOUTME: Wipe and re-clone the demo project dir from template.
# ABOUTME: Use between rehearsals (especially after running in "fail" mode).
#
# Usage: ./scripts/demo_reset.sh
#   Honors DEMO_DIR env var (defaults to ~/luthien-demo)

set -euo pipefail

cd "$(dirname "$0")/.."
TEMPLATE_DIR="$(pwd)/dev/demo/template"
DEMO_DIR="${DEMO_DIR:-${HOME}/luthien-demo}"

if [[ ! -d "${TEMPLATE_DIR}" ]]; then
    echo "ERROR: template not found at ${TEMPLATE_DIR}" >&2
    exit 1
fi

echo "==> Resetting ${DEMO_DIR} from ${TEMPLATE_DIR}"
rm -rf "${DEMO_DIR}"
mkdir -p "${DEMO_DIR}"
cp -R "${TEMPLATE_DIR}/." "${DEMO_DIR}/"

echo "==> Done. Files:"
find "${DEMO_DIR}" -type f | sed 's|^|    |'
