#!/usr/bin/env bash
# Requires: bash 3.2+
set -euo pipefail

echo "== Shellcheck (shell scripts) =="
if command -v shellcheck &>/dev/null; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    shellcheck_failed=0
    for script in "${SCRIPT_DIR}"/*.sh; do
        if [[ -f "$script" ]]; then
            echo "  Checking $(basename "$script")..."
            if ! shellcheck --shell=bash "$script"; then
                shellcheck_failed=1
            fi
        fi
    done
    if [[ "$shellcheck_failed" -ne 0 ]]; then
        echo "Shellcheck found issues. Please fix them before proceeding."
        exit 1
    fi
    echo "  All shell scripts passed."
else
    echo "  WARNING: shellcheck not installed, skipping shell script checks."
    echo "  Install with: brew install shellcheck (macOS) or apt-get install shellcheck (Linux)"
fi

echo "== Ruff format (apply) =="
uv run ruff format

echo "== Ruff lint (autofix) =="
uv run ruff check --fix

echo "== Ruff lint (E/F/I/D gating) =="
uv run ruff check

echo "== Ruff docstrings (report-only) =="
# Non-gating docstring report per maintainability plan
uv run ruff check --select D --exit-zero || true

echo "== Pyright (basic) =="
uv run pyright

echo "== Tests =="
uv run -m pytest -q

echo "== Radon complexity (report-only) =="
uv run radon cc -s -a src || true

echo "All checks completed."
