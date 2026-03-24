#!/usr/bin/env bash
# Requires: bash 3.2+
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

# ── Phase 1: Fix ──────────────────────────────────────────────

echo "== Dependency sync (locked) =="
uv sync --all-groups --locked

echo "== Shellcheck (shell scripts) =="
if command -v shellcheck &>/dev/null; then
    SCRIPT_DIR="$REPO_ROOT/scripts"
    shellcheck_failed=0
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
    echo "  ERROR: shellcheck not installed."
    echo "  Install with: brew install shellcheck (macOS) or apt-get install shellcheck (Linux)"
    exit 1
fi

echo "== Ruff format (apply) =="
uv run ruff format

echo "== Ruff lint (autofix) =="
uv run ruff check --fix

if ! git diff --quiet 2>/dev/null; then
    echo ""
    echo "── Formatting/lint produced changes. Auto-staging: ──"
    git diff --stat
    git add -u
    echo "── Staged. Include these in your next commit. ──"
    echo ""
fi

# ── Phase 2: Gate ─────────────────────────────────────────────

echo "== Ruff lint (E/F/I/D gating) =="
uv run ruff check

echo "== Ruff docstrings (report-only) =="
uv run ruff check --select D --exit-zero || true

echo "== Pyright (basic) =="
uv run pyright

echo "== Tests =="
uv run -m pytest -q

echo "== Radon complexity (report-only) =="
uv run radon cc -s -a src || true

echo "== Clean tree check (post) =="
if ! git diff --quiet 2>/dev/null; then
    echo "ERROR: Unexpected uncommitted changes after gating checks."
    git diff --stat
    exit 1
fi

echo ""
echo "All checks completed."

# Remind about staged/unpushed changes
if ! git diff --cached --quiet 2>/dev/null; then
  echo ""
  echo "⚠ Staged but uncommitted changes detected (includes auto-staged formatting fixes)."
  echo "  Commit and push before continuing."
fi

if git symbolic-ref --short HEAD >/dev/null 2>&1 && upstream=$(git rev-parse --abbrev-ref "@{upstream}" 2>/dev/null); then
  if [ "$(git rev-list "$upstream"..HEAD --count 2>/dev/null)" -gt 0 ]; then
    echo ""
    echo "⚠ Local commits not yet pushed to $upstream."
    echo "  Remember to push before continuing."
  fi
fi
