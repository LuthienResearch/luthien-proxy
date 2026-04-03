#!/usr/bin/env bash
# Fast sanity checks for active development.
# Same gates as dev_checks.sh minus coverage and report-only steps,
# with pyright + pytest running in parallel, and pytest using xdist.
# By default, only runs unit tests for changed files (since merge-base with main).
# Use dev_checks.sh for the full gate before PRs.
#
# Options:
#   --all    Run all unit tests, not just changed-file tests
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
RUN_ALL=0
for arg in "$@"; do
    case "$arg" in
        --all) RUN_ALL=1 ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

# ── Phase 1: Lint + shellcheck (fast, sequential — may fix files) ────

echo "== Ruff format =="
uv run ruff format

echo "== Ruff lint (autofix) =="
uv run ruff check --fix

echo "== Ruff lint (gate) =="
uv run ruff check

echo "== Shellcheck =="
SCRIPT_DIR="$REPO_ROOT/scripts"
shellcheck_failed=0
pushd "$SCRIPT_DIR" > /dev/null
for script in *.sh; do
    if [[ -f "$script" ]]; then
        if ! shellcheck --shell=bash -x "$script" 2>/dev/null; then
            shellcheck_failed=1
        fi
    fi
done
popd > /dev/null
if [[ "$shellcheck_failed" -ne 0 ]]; then
    echo "  Shellcheck found issues."
    exit 1
fi
echo "  All scripts passed."

# ── Phase 2: Resolve test targets ────────────────────────────────────

PYTEST_ARGS=(-p no:cacheprovider --no-cov -q)
test_target_msg="all unit tests"

if [ "$RUN_ALL" -eq 0 ]; then
    merge_base=$(git merge-base HEAD main 2>/dev/null || echo "")
    if [ -n "$merge_base" ] && [ "$(git rev-parse HEAD)" != "$merge_base" ]; then
        # Collect changed .py files (committed + staged + unstaged)
        changed_files=$(
            { git diff --name-only "$merge_base" HEAD -- '*.py'
              git diff --name-only -- '*.py'
              git diff --cached --name-only -- '*.py'
            } | sort -u
        )

        test_files=()
        while IFS= read -r f; do
            [ -z "$f" ] && continue

            # Changed unit test file — include if it's actually a test file
            if [[ "$f" == tests/luthien_proxy/unit_tests/*/test_*.py ]]; then
                if [ -f "$REPO_ROOT/$f" ]; then
                    test_files+=("$f")
                fi
                continue
            fi

            # Changed CLI test file — include if it's actually a test file
            if [[ "$f" == tests/luthien_cli/test_*.py ]]; then
                if [ -f "$REPO_ROOT/$f" ]; then
                    test_files+=("$f")
                fi
                continue
            fi

            # Changed conftest or fixture — run all tests (can't scope the impact)
            if [[ "$f" == tests/*/conftest.py ]] || [[ "$f" == tests/luthien_proxy/fixtures/* ]]; then
                test_files=()
                test_target_msg="all unit tests (conftest/fixture changed)"
                break
            fi

            # Proxy source file → corresponding unit test
            # src/luthien_proxy/foo/bar.py → tests/luthien_proxy/unit_tests/foo/test_bar.py
            if [[ "$f" == src/luthien_proxy/* ]]; then
                rel="${f#src/luthien_proxy/}"
                dir=$(dirname "$rel")
                base=$(basename "$rel" .py)
                test_path="tests/luthien_proxy/unit_tests/${dir}/test_${base}.py"
                if [ -f "$REPO_ROOT/$test_path" ]; then
                    test_files+=("$test_path")
                fi
                continue
            fi

            # CLI source file → corresponding test
            # src/luthien_cli/src/luthien_cli/commands/onboard.py → tests/luthien_cli/test_onboard.py
            # src/luthien_cli/src/luthien_cli/config.py → tests/luthien_cli/test_config.py
            if [[ "$f" == src/luthien_cli/src/luthien_cli/* ]]; then
                base=$(basename "$f" .py)
                test_path="tests/luthien_cli/test_${base}.py"
                if [ -f "$REPO_ROOT/$test_path" ]; then
                    test_files+=("$test_path")
                fi
                continue
            fi
        done <<< "$changed_files"

        # Deduplicate and set pytest args
        if [ ${#test_files[@]} -gt 0 ]; then
            mapfile -t test_files < <(printf '%s\n' "${test_files[@]}" | sort -u)
            PYTEST_ARGS+=("${test_files[@]}")
            test_target_msg="${#test_files[@]} changed-file test(s)"
        elif [[ "$test_target_msg" != *"conftest"* ]]; then
            # No test files matched and not a conftest trigger — run all as safety net
            test_target_msg="all unit tests (no test files matched changes)"
        fi
    fi
fi

# Use xdist only when running enough tests to justify worker startup overhead
if [[ "$test_target_msg" == "all unit"* ]]; then
    PYTEST_ARGS+=(-n auto)
fi

# ── Phase 3: Type check + tests (parallel) ───────────────────────────

PYRIGHT_LOG=$(mktemp)
PYTEST_LOG=$(mktemp)
trap 'rm -f "$PYRIGHT_LOG" "$PYTEST_LOG"' EXIT

pyright_failed=0
pytest_failed=0

echo "== Pyright + Pytest [${test_target_msg}] (parallel) =="

uv run pyright > "$PYRIGHT_LOG" 2>&1 &
pid_pyright=$!

uv run pytest "${PYTEST_ARGS[@]}" > "$PYTEST_LOG" 2>&1 &
pid_pytest=$!

wait "$pid_pyright" || pyright_failed=1
wait "$pid_pytest"  || pytest_failed=1

if [ "$pyright_failed" -ne 0 ]; then
    echo ""
    echo "── Pyright FAILED ──"
    cat "$PYRIGHT_LOG"
fi

if [ "$pytest_failed" -ne 0 ]; then
    echo ""
    echo "── Pytest FAILED ──"
    cat "$PYTEST_LOG"
fi

if [ "$pyright_failed" -ne 0 ] || [ "$pytest_failed" -ne 0 ]; then
    if [ "$pyright_failed" -eq 0 ]; then
        echo ""
        echo "── Pyright passed ──"
        tail -1 "$PYRIGHT_LOG"
    fi
    if [ "$pytest_failed" -eq 0 ]; then
        echo ""
        echo "── Pytest passed ──"
        tail -3 "$PYTEST_LOG"
    fi
    exit 1
fi

# Both passed — show summaries
echo ""
tail -1 "$PYRIGHT_LOG"
tail -3 "$PYTEST_LOG"
echo ""
echo "All fast checks passed."
