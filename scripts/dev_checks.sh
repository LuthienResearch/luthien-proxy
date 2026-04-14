#!/usr/bin/env bash
# Requires: bash 3.2+
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

TIMING_FILE=""
for arg in "$@"; do
    case "$arg" in
        --timing)
            TIMING_FILE="$REPO_ROOT/.dev_checks_timings.jsonl"
            ;;
        --timing=*)
            TIMING_FILE="${arg#--timing=}"
            ;;
    esac
done

if [[ -n "$TIMING_FILE" ]]; then
    : > "$TIMING_FILE"
    echo "Timing output: $TIMING_FILE"
fi

RUN_ID="$(date -u +%Y-%m-%dT%H:%M:%SZ)-$$"
TOTAL_START=$(date +%s.%N)

# Run a named step and optionally record its wall-clock duration + exit status
# as one JSON line in $TIMING_FILE.
step() {
    local name="$1"
    shift
    if [[ -z "$TIMING_FILE" ]]; then
        "$@"
        return $?
    fi
    local start end dur rc
    start=$(date +%s.%N)
    set +e
    "$@"
    rc=$?
    set -e
    end=$(date +%s.%N)
    dur=$(awk -v s="$start" -v e="$end" 'BEGIN { printf "%.3f", e - s }')
    printf '{"run_id":"%s","step":"%s","duration_s":%s,"exit_code":%d,"ts":"%s"}\n' \
        "$RUN_ID" "$name" "$dur" "$rc" "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" \
        >> "$TIMING_FILE"
    return $rc
}

# ── Phase 1: Fix ──────────────────────────────────────────────

echo "== Dependency sync (locked) =="
step "uv_sync" uv sync --all-groups --locked

echo "== Shellcheck (shell scripts) =="
run_shellcheck() {
    if ! command -v shellcheck &>/dev/null; then
        echo "  ERROR: shellcheck not installed."
        echo "  Install with: brew install shellcheck (macOS) or apt-get install shellcheck (Linux)"
        return 1
    fi
    local script_dir="$REPO_ROOT/scripts"
    local failed=0
    pushd "$script_dir" > /dev/null
    for script in *.sh; do
        if [[ -f "$script" ]]; then
            echo "  Checking $script..."
            if ! shellcheck --shell=bash -x "$script"; then
                failed=1
            fi
        fi
    done
    popd > /dev/null
    if [[ "$failed" -ne 0 ]]; then
        echo "Shellcheck found issues. Please fix them before proceeding."
        return 1
    fi
    echo "  All shell scripts passed."
}
step "shellcheck" run_shellcheck

echo "== Generate settings.py from config_fields =="
step "generate_settings" uv run python scripts/generate_settings.py

echo "== Generate .env.example from config_fields =="
run_generate_env() { uv run python scripts/generate_env_example.py > .env.example; }
step "generate_env_example" run_generate_env

DIRTY_BEFORE=$(git diff --name-only 2>/dev/null)

echo "== Ruff format (apply) =="
step "ruff_format" uv run ruff format

echo "== Ruff lint (autofix) =="
step "ruff_check_fix" uv run ruff check --fix

DIRTY_AFTER=$(git diff --name-only 2>/dev/null)
FORMATTER_CHANGED=$(comm -13 <(echo "$DIRTY_BEFORE" | sort) <(echo "$DIRTY_AFTER" | sort))

if [ -n "$FORMATTER_CHANGED" ]; then
    echo ""
    echo "── Formatting/lint produced changes. Auto-staging: ──"
    echo "$FORMATTER_CHANGED" | while read -r f; do
        echo "  $f"
        git add -- "$f"
    done
    echo "── Staged. Include these in your next commit. ──"
    echo ""
fi

# ── Phase 2: Gate ─────────────────────────────────────────────

echo "== Ruff lint (E/F/I/D gating) =="
step "ruff_check" uv run ruff check

echo "== Ruff docstrings (report-only) =="
run_ruff_docstrings() { uv run ruff check --select D --exit-zero || true; }
step "ruff_docstrings" run_ruff_docstrings

echo "== Pyright (basic) =="
step "pyright" uv run pyright

echo "== Tests =="
step "pytest" uv run -m pytest -q

echo "== Radon complexity (report-only) =="
run_radon() { uv run radon cc -s -a src || true; }
step "radon" run_radon

echo "== Clean tree check (post) =="
if ! git diff --quiet 2>/dev/null; then
    echo "ERROR: Unexpected uncommitted changes after gating checks."
    git diff --stat
    exit 1
fi

echo ""
echo "All checks completed."

if [[ -n "$TIMING_FILE" ]]; then
    TOTAL_END=$(date +%s.%N)
    TOTAL=$(awk -v s="$TOTAL_START" -v e="$TOTAL_END" 'BEGIN { printf "%.3f", e - s }')
    printf '{"run_id":"%s","step":"__total__","duration_s":%s,"exit_code":0,"ts":"%s"}\n' \
        "$RUN_ID" "$TOTAL" "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" \
        >> "$TIMING_FILE"
    echo ""
    echo "── Timing summary ──"
    awk -F'[,:]' '
        /"step"/ {
            for (i=1; i<=NF; i++) {
                if ($i ~ /"step"/) { gsub(/["{} ]/, "", $(i+1)); step=$(i+1) }
                if ($i ~ /"duration_s"/) { gsub(/["{} ]/, "", $(i+1)); dur=$(i+1) }
            }
            printf "  %7.2fs  %s\n", dur, step
        }
    ' "$TIMING_FILE" | sort -rn
fi

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
