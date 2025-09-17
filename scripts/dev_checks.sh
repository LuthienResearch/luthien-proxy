#!/usr/bin/env bash
set -euo pipefail

echo "== Ruff format (check) =="
uv run ruff format --check || true

echo "== Ruff lint (E/F/I gating) =="
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
