#!/usr/bin/env bash
set -euo pipefail

echo "== Shellcheck (shell scripts) =="
if command -v shellcheck &>/dev/null; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
    shellcheck "$PROJECT_ROOT"/scripts/*.sh "$PROJECT_ROOT"/docker/*.sh
    echo "Shellcheck passed."
else
    echo "shellcheck not installed — skipping (install: brew install shellcheck)"
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
