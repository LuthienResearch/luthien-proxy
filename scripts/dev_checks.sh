#!/usr/bin/env bash
# Requires: bash 3.2+
set -euo pipefail

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

# Remind about uncommitted/unpushed changes
if ! git diff --quiet 2>/dev/null; then
  echo ""
  echo "⚠ Unstaged changes detected (likely from formatting/lint fixes)."
  echo "  Remember to commit and push before continuing."
fi

if ! git diff --cached --quiet 2>/dev/null; then
  echo ""
  echo "⚠ Staged but uncommitted changes detected."
  echo "  Remember to commit and push before continuing."
fi

if git symbolic-ref --short HEAD >/dev/null 2>&1 && upstream=$(git rev-parse --abbrev-ref "@{upstream}" 2>/dev/null); then
  if [ "$(git rev-list "$upstream"..HEAD --count 2>/dev/null)" -gt 0 ]; then
    echo ""
    echo "⚠ Local commits not yet pushed to $upstream."
    echo "  Remember to push before continuing."
  fi
fi
