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

# Portable high-resolution epoch timestamp. `date +%N` is a GNU coreutils
# extension; BSD/macOS `date` emits the literal 'N' instead of nanoseconds.
# Try GNU date first, then gdate (common via `brew install coreutils`), and
# finally fall back to whole-second resolution. The JSONL schema is unchanged.
if date +%N 2>/dev/null | grep -qE '^[0-9]+$'; then
    now_epoch()      { date +%s.%N; }
    now_iso_ms_utc() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
elif command -v gdate >/dev/null 2>&1 && gdate +%N 2>/dev/null | grep -qE '^[0-9]+$'; then
    now_epoch()      { gdate +%s.%N; }
    now_iso_ms_utc() { gdate -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
else
    now_epoch()      { date +%s; }
    now_iso_ms_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
fi

if [[ -n "$TIMING_FILE" ]]; then
    : > "$TIMING_FILE"
    echo "Timing output: $TIMING_FILE"
fi

RUN_ID="$(date -u +%Y-%m-%dT%H:%M:%SZ)-$$"
TOTAL_START=$(now_epoch)

# Always print the summary on exit (including on failure) when timing is on,
# so slow/failing runs are the most useful to diagnose.
print_timing_summary() {
    [[ -z "$TIMING_FILE" ]] && return
    [[ ! -s "$TIMING_FILE" ]] && return
    echo ""
    echo "── Timing summary ──"
    # Parse JSONL via Python for correctness (field order / embedded colons).
    # uv is already warm from earlier steps so the overhead is negligible.
    uv run python - "$TIMING_FILE" <<'PY' | sort -rn
import json, sys
for line in open(sys.argv[1]):
    line = line.strip()
    if not line:
        continue
    try:
        rec = json.loads(line)
    except Exception:
        continue
    print(f"  {float(rec['duration_s']):7.2f}s  {rec['step']}")
PY
}
trap print_timing_summary EXIT

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
    start=$(now_epoch)
    set +e
    "$@"
    rc=$?
    set -e
    end=$(now_epoch)
    dur=$(awk -v s="$start" -v e="$end" 'BEGIN { printf "%.3f", e - s }')
    printf '{"run_id":"%s","step":"%s","duration_s":%s,"exit_code":%d,"ts":"%s"}\n' \
        "$RUN_ID" "$name" "$dur" "$rc" "$(now_iso_ms_utc)" \
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

echo "== Pyright + Tests (parallel) =="
# Pyright and pytest are independent; running them concurrently saves ~10s.
# Each writes its output + end-timestamp + exit code to its own tmp files,
# so we can record each process's actual finish time (not just when the
# outer wait returns). This matters once pyright ≈ pytest in duration.
PYRIGHT_LOG="$(mktemp)"
PYTEST_LOG="$(mktemp)"
PYRIGHT_END_FILE="$(mktemp)"
PYTEST_END_FILE="$(mktemp)"
PYRIGHT_PID=""
PYTEST_PID=""

parallel_cleanup() {
    # Kill any still-running background jobs (e.g. on Ctrl-C) so they don't
    # become orphans that keep burning CPU after the script exits.
    if [[ -n "$PYRIGHT_PID" ]] && kill -0 "$PYRIGHT_PID" 2>/dev/null; then
        kill "$PYRIGHT_PID" 2>/dev/null || true
    fi
    if [[ -n "$PYTEST_PID" ]] && kill -0 "$PYTEST_PID" 2>/dev/null; then
        kill "$PYTEST_PID" 2>/dev/null || true
    fi
    rm -f "$PYRIGHT_LOG" "$PYTEST_LOG" "$PYRIGHT_END_FILE" "$PYTEST_END_FILE"
}
# Compose with the existing summary trap instead of replacing it.
trap 'parallel_cleanup; print_timing_summary' EXIT INT TERM

pyright_start=$(now_epoch)
( uv run pyright > "$PYRIGHT_LOG" 2>&1; rc=$?; now_epoch > "$PYRIGHT_END_FILE"; exit "$rc" ) &
PYRIGHT_PID=$!

pytest_start=$(now_epoch)
( uv run -m pytest -q > "$PYTEST_LOG" 2>&1; rc=$?; now_epoch > "$PYTEST_END_FILE"; exit "$rc" ) &
PYTEST_PID=$!

set +e
wait "$PYRIGHT_PID"; PYRIGHT_RC=$?
wait "$PYTEST_PID";  PYTEST_RC=$?
set -e
pyright_end=$(cat "$PYRIGHT_END_FILE")
pytest_end=$(cat "$PYTEST_END_FILE")

echo ""
echo "── Pyright output ──"
cat "$PYRIGHT_LOG"
echo ""
echo "── Pytest output ──"
cat "$PYTEST_LOG"

if [[ -n "$TIMING_FILE" ]]; then
    pyright_dur=$(awk -v s="$pyright_start" -v e="$pyright_end" 'BEGIN { printf "%.3f", e - s }')
    pytest_dur=$(awk -v s="$pytest_start" -v e="$pytest_end" 'BEGIN { printf "%.3f", e - s }')
    printf '{"run_id":"%s","step":"pyright","duration_s":%s,"exit_code":%d,"ts":"%s"}\n' \
        "$RUN_ID" "$pyright_dur" "$PYRIGHT_RC" "$(now_iso_ms_utc)" \
        >> "$TIMING_FILE"
    printf '{"run_id":"%s","step":"pytest","duration_s":%s,"exit_code":%d,"ts":"%s"}\n' \
        "$RUN_ID" "$pytest_dur" "$PYTEST_RC" "$(now_iso_ms_utc)" \
        >> "$TIMING_FILE"
fi

# If both failed, report both before exiting (not just the first one).
if [[ $PYRIGHT_RC -ne 0 || $PYTEST_RC -ne 0 ]]; then
    [[ $PYRIGHT_RC -ne 0 ]] && echo "Pyright failed (exit $PYRIGHT_RC)."
    [[ $PYTEST_RC  -ne 0 ]] && echo "Pytest failed (exit $PYTEST_RC)."
    # Prefer pytest's exit code when both fail (it's the more actionable signal).
    if [[ $PYTEST_RC -ne 0 ]]; then
        exit "$PYTEST_RC"
    fi
    exit "$PYRIGHT_RC"
fi

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
    TOTAL_END=$(now_epoch)
    TOTAL=$(awk -v s="$TOTAL_START" -v e="$TOTAL_END" 'BEGIN { printf "%.3f", e - s }')
    printf '{"run_id":"%s","step":"__total__","duration_s":%s,"exit_code":0,"ts":"%s"}\n' \
        "$RUN_ID" "$TOTAL" "$(now_iso_ms_utc)" \
        >> "$TIMING_FILE"
fi
# Summary itself is printed by the EXIT trap (runs on success and failure).

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
