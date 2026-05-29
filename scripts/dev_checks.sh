#!/usr/bin/env bash
# Requires: bash 3.2+
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

TIMING_FILE=""
SKIP_REPORTS=0
PYTEST_WORKERS="${DEV_CHECKS_PYTEST_WORKERS:-4}"
for arg in "$@"; do
    case "$arg" in
        --timing)
            TIMING_FILE="$REPO_ROOT/.dev_checks_timings.jsonl"
            ;;
        --timing=*)
            TIMING_FILE="${arg#--timing=}"
            ;;
        --skip-reports)
            SKIP_REPORTS=1
            ;;
        --fast)
            # --fast is shorthand for the current fastest inner-loop combo.
            # Today that's just --skip-reports. Additional knobs may be added
            # here in the future without breaking --skip-reports' meaning.
            SKIP_REPORTS=1
            ;;
        --workers=*)
            PYTEST_WORKERS="${arg#--workers=}"
            ;;
        --help|-h)
            cat <<'USAGE'
Usage: dev_checks.sh [--timing[=PATH]] [--skip-reports] [--fast] [--workers=N]

  --timing[=PATH]  Record per-step wall-clock timings as JSONL
                   (default path: .dev_checks_timings.jsonl).
  --skip-reports   Skip report-only steps (ruff docstrings, radon) and
                   pytest coverage. Gating checks (ruff, pyright, pytest)
                   still run. Saves ~13s.
  --fast           Alias for --skip-reports (may enable more inner-loop
                   shortcuts in the future).
  --workers=N      Pytest parallel workers (pytest-xdist). Default: 4.
                   Use 1 to disable parallelism (helpful for debugging
                   flakes or interleaved output). Also overridable via
                   DEV_CHECKS_PYTEST_WORKERS env var.
USAGE
            exit 0
            ;;
        *)
            echo "dev_checks.sh: unknown argument '$arg'" >&2
            echo "Run 'dev_checks.sh --help' for usage." >&2
            exit 2
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
    # Recurse into subdirs (e.g. scripts/automated_maintenance/) so nested scripts are linted.
    # `-print0` + `read -d ''` handles paths with spaces safely.
    while IFS= read -r -d '' script; do
        rel="${script#"${script_dir}"/}"
        echo "  Checking ${rel}..."
        # `-P SCRIPTDIR` lets shellcheck resolve relative `# shellcheck
        # source=...` directives against the script's own directory.
        if ! shellcheck --shell=bash -x -P SCRIPTDIR "$script"; then
            failed=1
        fi
    done < <(find "$script_dir" -type f -name '*.sh' -print0)
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

if [[ $SKIP_REPORTS -eq 0 ]]; then
    echo "== Ruff docstrings (report-only) =="
    run_ruff_docstrings() { uv run ruff check --select D --exit-zero || true; }
    step "ruff_docstrings" run_ruff_docstrings
fi

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
# Compose with the existing summary trap instead of replacing it. On Ctrl-C
# the signal handler must explicitly exit — otherwise control returns to the
# interrupted `wait` and the script falls through to `cat`-ing now-deleted
# logs and past the gate. Clear the EXIT trap first so cleanup runs once.
trap 'parallel_cleanup; print_timing_summary' EXIT
trap 'trap - EXIT; parallel_cleanup; print_timing_summary; exit 130' INT TERM

# `set +e` inside each subshell: the outer script runs under `set -e`, which
# is inherited by `(...)`. Without it, a failing pyright/pytest aborts the
# subshell at the command itself, so the end-timestamp + `exit "$rc"` never
# run and the timing record is broken on exactly the failed runs you most
# want to measure. The real exit code is still captured by `wait` below.
pyright_start=$(now_epoch)
( set +e; uv run pyright > "$PYRIGHT_LOG" 2>&1; rc=$?; now_epoch > "$PYRIGHT_END_FILE"; exit "$rc" ) &
PYRIGHT_PID=$!

pytest_start=$(now_epoch)
PYTEST_PARALLEL=()
if [[ "$PYTEST_WORKERS" -gt 1 ]]; then
    PYTEST_PARALLEL=(-n "$PYTEST_WORKERS")
fi
# `${arr[@]+"${arr[@]}"}` expands safely under `set -u` on bash 3.2 (macOS),
# where a bare `"${arr[@]}"` on an empty array trips "unbound variable".
if [[ $SKIP_REPORTS -eq 1 ]]; then
    ( set +e; uv run -m pytest -q --no-cov ${PYTEST_PARALLEL[@]+"${PYTEST_PARALLEL[@]}"} > "$PYTEST_LOG" 2>&1; rc=$?; now_epoch > "$PYTEST_END_FILE"; exit "$rc" ) &
else
    ( set +e; uv run -m pytest -q ${PYTEST_PARALLEL[@]+"${PYTEST_PARALLEL[@]}"} > "$PYTEST_LOG" 2>&1; rc=$?; now_epoch > "$PYTEST_END_FILE"; exit "$rc" ) &
fi
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

if [[ $SKIP_REPORTS -eq 0 ]]; then
    echo "== Radon complexity (report-only) =="
    run_radon() { uv run radon cc -s -a src || true; }
    step "radon" run_radon
fi

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
