#!/usr/bin/env bash
# Requires: bash 3.2+
set -euo pipefail

echo "== Shellcheck (shell scripts) =="
if command -v shellcheck &>/dev/null; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    shellcheck_failed=0
    # Run from the scripts directory so relative source= directives resolve correctly
    pushd "$SCRIPT_DIR" > /dev/null
    for script in *.sh; do
        if [[ -f "$script" ]]; then
            echo "  Checking $script..."
            if ! shellcheck --shell=bash -x "$script"; then
                shellcheck_failed=1
            fi
        fi
    done
    popd > /dev/null
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
