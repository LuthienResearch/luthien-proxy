#!/usr/bin/env bash
# shellcheck disable=SC2317 # Functions called indirectly via "run_$tier" and trap
# ABOUTME: Orchestrates e2e test tiers with automatic setup and teardown.
# ABOUTME: Handles sqlite_e2e (no infra), mock_e2e (in-process), and real e2e (Docker + API key).
#
# Usage:
#   ./scripts/run_e2e.sh              # Run all available tiers
#   ./scripts/run_e2e.sh sqlite       # SQLite only (no Docker needed)
#   ./scripts/run_e2e.sh mock         # Mock e2e only (in-process gateway)
#   ./scripts/run_e2e.sh real         # Real API only (starts Docker, needs ANTHROPIC_API_KEY)
#   ./scripts/run_e2e.sh sqlite mock  # Multiple tiers
#
# Flags:
#   --fresh               Reset stepwise state, start from the beginning
#   --no-log              Don't write to log file (stdout only)
#
# Extra pytest args after --:
#   ./scripts/run_e2e.sh sqlite -- -k "test_streaming" -vv
#
# Behavior:
#   - Stops on first failure (pytest --stepwise)
#   - Resumes from last failure on re-run (pytest --stepwise)
#   - Logs to .e2e-logs/<timestamp>.log (use --no-log to disable)
#   - Per-test timeouts: sqlite=60s, mock=30s, real=120s
#   - Use --fresh to reset and start from scratch

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# Tier result tracking (bash 3.x compatible — no associative arrays)
TIER_RESULT_sqlite=""
TIER_RESULT_mock=""
TIER_RESULT_real=""

_set_tier_result() { eval "TIER_RESULT_$1=\"$2\""; }
_get_tier_result() { eval "echo \"\${TIER_RESULT_$1:-unknown}\""; }

info()  { echo -e "${BLUE}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*"; }
header() { echo -e "\n${BOLD}═══ $* ═══${NC}"; }

# Parse arguments: flags and tier names before --, pytest args after --
TIERS=()
PYTEST_EXTRA=()
FRESH=false
NO_LOG=false
saw_separator=false
for arg in "$@"; do
    if [[ "$arg" == "--" ]]; then
        saw_separator=true
        continue
    fi
    if $saw_separator; then
        PYTEST_EXTRA+=("$arg")
    elif [[ "$arg" == "--fresh" ]]; then
        FRESH=true
    elif [[ "$arg" == "--no-log" ]]; then
        NO_LOG=true
    else
        TIERS+=("$arg")
    fi
done

# Default: run all tiers
if [[ ${#TIERS[@]} -eq 0 ]]; then
    TIERS=(sqlite mock real)
fi

# Validate tier names
for tier in "${TIERS[@]}"; do
    case "$tier" in
        sqlite|mock|real) ;;
        *) fail "Unknown tier: $tier (expected: sqlite, mock, real)"; exit 1 ;;
    esac
done

# --- Logging ---

LOG_DIR="$REPO_ROOT/.e2e-logs"
LOG_FILE=""
if ! $NO_LOG; then
    mkdir -p "$LOG_DIR"
    LOG_FILE="$LOG_DIR/$(date +%Y%m%d-%H%M%S).log"
    # Tee all output to both terminal and log file
    exec > >(tee -a "$LOG_FILE") 2>&1
fi

# --- Stepwise (resume from last failure) ---

STEPWISE_ARGS=(--stepwise)
if $FRESH; then
    STEPWISE_ARGS=(--stepwise --sw-reset)
    info "Fresh run: stepwise state reset"
fi

# --- Port helpers ---

_is_port_free() {
    local port="$1"
    if command -v ss &>/dev/null; then
        if ss -tlnH "sport = :${port}" | grep -q .; then return 1; else return 0; fi
    else
        if (echo >/dev/tcp/localhost/"${port}") 2>/dev/null; then return 1; else return 0; fi
    fi
}

_find_free_port() {
    local port="$1"
    local i=0
    while [[ $i -lt 100 ]]; do
        if _is_port_free "$port"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
        i=$((i + 1))
    done
    fail "No free port found starting from $1"
    return 1
}

# --- Docker helpers ---

COMPOSE_PROJECT=""

compose_project_name() {
    # Derive unique project name from directory (same logic as quick_start.sh)
    local dir_name
    dir_name="$(basename "$REPO_ROOT")"
    if [[ "$dir_name" == "luthien-proxy" ]]; then
        echo "luthien-proxy-e2e"
    else
        echo "luthien-e2e-${dir_name}"
    fi
}

docker_up() {
    local compose_files=("$@")
    COMPOSE_PROJECT="$(compose_project_name)"

    # Auto-select free ports for Docker services (Postgres, Redis, Gateway)
    set +u
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/find-available-ports.sh"
    set -u

    info "Starting Docker services (project: $COMPOSE_PROJECT)..."
    local cmd=(docker compose -p "$COMPOSE_PROJECT" --env-file "${ENV_FILE:-$REPO_ROOT/.env}")
    for f in "${compose_files[@]}"; do
        cmd+=(-f "$f")
    done
    "${cmd[@]}" up -d --wait --build 2>&1 | sed 's/^/  /'

    # Wait for gateway health (60s timeout — Docker builds can be slow on first run)
    local gateway_url="http://localhost:${GATEWAY_PORT:-8000}"
    info "Waiting for gateway at $gateway_url..."
    local attempts=0
    local max_attempts=60
    while ! curl -sf "$gateway_url/health" > /dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [[ $attempts -ge $max_attempts ]]; then
            fail "Gateway not ready after ${max_attempts}s"
            docker compose -p "$COMPOSE_PROJECT" --env-file "${ENV_FILE:-$REPO_ROOT/.env}" logs gateway 2>&1 | tail -20
            return 1
        fi
        sleep 1
    done
    ok "Gateway healthy on port ${GATEWAY_PORT:-8000}"
}

docker_down() {
    if [[ -n "$COMPOSE_PROJECT" ]]; then
        info "Tearing down Docker services..."
        docker compose -p "$COMPOSE_PROJECT" --env-file "${ENV_FILE:-$REPO_ROOT/.env}" down --remove-orphans 2>&1 | sed 's/^/  /'
        COMPOSE_PROJECT=""
    fi
}

# Track background PIDs for cleanup
MOCK_GATEWAY_PID=""

# Cleanup on exit
cleanup() {
    if [[ -n "$MOCK_GATEWAY_PID" ]]; then
        kill "$MOCK_GATEWAY_PID" 2>/dev/null
        wait "$MOCK_GATEWAY_PID" 2>/dev/null
        MOCK_GATEWAY_PID=""
    fi
    docker_down
}
trap cleanup EXIT

# --- Check prerequisites ---

check_docker() {
    if ! command -v docker &> /dev/null; then
        fail "Docker not installed"
        return 1
    fi
    if ! docker compose version &> /dev/null; then
        fail "Docker Compose v2 not available"
        return 1
    fi
    return 0
}

# Source .env — check worktree root first, then follow git worktree pointer
# to the main repo (worktrees share gitignored files like .env with main).
ENV_FILE="$REPO_ROOT/.env"
if [[ ! -f "$ENV_FILE" && -f "$REPO_ROOT/.git" ]]; then
    # In a git worktree: .git is a file pointing to the main repo
    MAIN_ROOT="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null | xargs dirname)"
    if [[ -f "$MAIN_ROOT/.env" ]]; then
        ENV_FILE="$MAIN_ROOT/.env"
    fi
fi
if [[ -f "$ENV_FILE" ]]; then
    info "Loading env from $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# --- Tier: sqlite ---

run_sqlite() {
    header "SQLite E2E Tests (in-process, no Docker)"

    info "Running tests..."
    local exit_code=0
    uv run pytest \
        -m sqlite_e2e \
        tests/luthien_proxy/e2e_tests/sqlite/ \
        -v --timeout=60 --no-cov \
        "${STEPWISE_ARGS[@]}" \
        ${PYTEST_EXTRA[@]+"${PYTEST_EXTRA[@]}"} \
    || exit_code=$?

    return "$exit_code"
}

# --- Tier: mock ---

run_mock() {
    header "Mock E2E Tests (in-process gateway + mock Anthropic server)"

    # Start an in-process gateway + mock server (no Docker needed)
    info "Starting in-process gateway..."
    local config_json gateway_pid
    config_json="$(mktemp /tmp/mock-gateway-XXXXXX.json)"

    uv run python scripts/start_mock_gateway.py > "$config_json" &
    gateway_pid=$!
    MOCK_GATEWAY_PID=$gateway_pid

    # Wait for the helper to print its config (up to 15s)
    local attempts=0
    while [[ ! -s "$config_json" ]]; do
        if ! kill -0 "$gateway_pid" 2>/dev/null; then
            fail "Gateway process died during startup"
            rm -f "$config_json"
            return 1
        fi
        attempts=$((attempts + 1))
        if [[ $attempts -ge 15 ]]; then
            fail "Gateway did not start within 15s"
            kill "$gateway_pid" 2>/dev/null
            rm -f "$config_json"
            return 1
        fi
        sleep 1
    done

    # Read and validate config from the helper (single process, validates JSON)
    local config_vals
    config_vals="$(uv run python -c "
import json, sys
try:
    d = json.load(open('$config_json'))
    print(d['gateway_url'], d['mock_port'], d['api_key'], d['admin_api_key'])
except (json.JSONDecodeError, KeyError) as e:
    print(f'Invalid config JSON: {e}', file=sys.stderr)
    sys.exit(1)
")" || { fail "Failed to parse gateway config"; rm -f "$config_json"; return 1; }
    read -r gw_url mock_port gw_api_key gw_admin_key <<< "$config_vals"
    rm -f "$config_json"

    ok "Gateway ready at $gw_url (mock on port $mock_port)"

    export E2E_GATEWAY_URL="$gw_url"
    export E2E_API_KEY="$gw_api_key"
    export E2E_ADMIN_API_KEY="$gw_admin_key"
    export MOCK_ANTHROPIC_PORT="$mock_port"
    export MOCK_ANTHROPIC_HOST="localhost"
    export ENABLE_REQUEST_LOGGING="true"

    info "Running tests..."
    local exit_code=0
    uv run pytest \
        -m mock_e2e \
        tests/luthien_proxy/e2e_tests/ \
        -v --timeout=30 --no-cov \
        "${STEPWISE_ARGS[@]}" \
        ${PYTEST_EXTRA[@]+"${PYTEST_EXTRA[@]}"} \
    || exit_code=$?

    # Shut down the in-process gateway (also cleaned up by trap EXIT)
    kill "$gateway_pid" 2>/dev/null
    wait "$gateway_pid" 2>/dev/null
    MOCK_GATEWAY_PID=""
    return "$exit_code"
}

# --- Tier: real ---

run_real() {
    header "Real E2E Tests (Docker + Anthropic API)"

    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        echo ""
        fail "╔══════════════════════════════════════════════════════════════╗"
        fail "║  SKIPPING REAL E2E TIER: ANTHROPIC_API_KEY not set          ║"
        fail "║                                                             ║"
        fail "║  Set it in .env or export it to run real API tests.         ║"
        fail "╚══════════════════════════════════════════════════════════════╝"
        echo ""
        _set_tier_result "real" "skipped"
        return 2
    fi

    if ! check_docker; then
        echo ""
        fail "╔══════════════════════════════════════════════════════════════╗"
        fail "║  SKIPPING REAL E2E TIER: Docker not available               ║"
        fail "╚══════════════════════════════════════════════════════════════╝"
        echo ""
        _set_tier_result "real" "skipped"
        return 2
    fi

    docker_up docker-compose.yaml

    export E2E_GATEWAY_URL="http://localhost:${GATEWAY_PORT:-8000}"

    info "Running tests..."
    local exit_code=0
    uv run pytest \
        -m "e2e and not mock_e2e and not sqlite_e2e" \
        tests/luthien_proxy/e2e_tests/ \
        -v --timeout=120 --no-cov \
        "${STEPWISE_ARGS[@]}" \
        ${PYTEST_EXTRA[@]+"${PYTEST_EXTRA[@]}"} \
    || exit_code=$?

    docker_down
    return "$exit_code"
}

# --- Main ---

echo -e "${BOLD}Luthien E2E Test Runner${NC}"
echo "Tiers: ${TIERS[*]}"
$FRESH && echo "Mode: fresh (stepwise state reset)"
[[ -n "$LOG_FILE" ]] && echo "Log: $LOG_FILE"
[[ ${#PYTEST_EXTRA[@]} -gt 0 ]] && echo "Extra pytest args: ${PYTEST_EXTRA[*]}"
echo ""

overall_exit=0
any_failed=false

for tier in "${TIERS[@]}"; do
    exit_code=0
    "run_$tier" || exit_code=$?

    case $exit_code in
        0) _set_tier_result "$tier" "passed"; ok "$tier: all tests passed" ;;
        2)
            # exit code 2 = tier self-skipped (no API key, no Docker)
            # BUT run_real returns 2 for skip; pytest --stepwise also returns 2.
            # Distinguish: if run_$tier set TIER_RESULTS already, it was a skip.
            if [[ "$(_get_tier_result "$tier")" == "skipped" ]]; then
                warn "$tier: skipped"
            else
                _set_tier_result "$tier" "failed"
                fail "$tier: tests failed (stepwise stopped, exit $exit_code)"
                overall_exit=1
                any_failed=true
                warn "Stopping — fix the failure and re-run to resume"
                break
            fi
            ;;
        *)
            _set_tier_result "$tier" "failed"
            fail "$tier: tests failed (exit $exit_code)"
            overall_exit=1
            any_failed=true
            # Stepwise: stop running further tiers on failure
            warn "Stopping — fix the failure and re-run to resume"
            break
            ;;
    esac
done

# If stepwise resumed and all remaining tests passed, do a full verification
# pass to catch regressions in tests before the resume point.
if ! $any_failed && ! $FRESH; then
    # Check if any tier's .pytest_cache has stepwise state, meaning we resumed
    stepwise_file="$REPO_ROOT/.pytest_cache/v/cache/stepwise"
    if [[ -f "$stepwise_file" ]]; then
        header "Verification Pass (checking for regressions)"
        info "Stepwise tests passed — re-running all tests to verify no regressions"
        STEPWISE_ARGS=(--stepwise --sw-reset)
        overall_exit=0

        for tier in "${TIERS[@]}"; do
            result="${TIER_RESULTS[$tier]:-unknown}"
            [[ "$result" == "skipped" ]] && continue

            exit_code=0
            "run_$tier" || exit_code=$?

            case $exit_code in
                0) ok "$tier: verification passed" ;;
                *)
                    _set_tier_result "$tier" "failed"
                    fail "$tier: regression detected in verification pass (exit $exit_code)"
                    overall_exit=1
                    break
                    ;;
            esac
        done
    fi
fi

# --- Summary ---

header "Results"
skipped_count=0
for tier in "${TIERS[@]}"; do
    result="$(_get_tier_result "$tier")"
    case $result in
        passed)  ok   "$tier" ;;
        skipped) warn "$tier (SKIPPED)"; skipped_count=$((skipped_count + 1)) ;;
        failed)  fail "$tier" ;;
    esac
done

if [[ $skipped_count -gt 0 ]]; then
    echo ""
    warn "⚠️  $skipped_count tier(s) were SKIPPED — see messages above for details"
fi

if [[ $overall_exit -ne 0 ]]; then
    echo ""
    fail "To resume from the last failure:"
    fail "  ./scripts/run_e2e.sh ${TIERS[*]}"
    fail "To start fresh:"
    fail "  ./scripts/run_e2e.sh --fresh ${TIERS[*]}"
fi

if [[ -n "$LOG_FILE" ]]; then
    echo ""
    info "Full log: $LOG_FILE"
fi

exit $overall_exit
