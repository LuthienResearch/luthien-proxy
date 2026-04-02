#!/usr/bin/env bash
# shellcheck disable=SC2317 # Functions called indirectly via "run_$tier" and trap
# ABOUTME: Orchestrates e2e test tiers with automatic setup and teardown.
# ABOUTME: Handles sqlite_e2e (no infra), mock_e2e (Docker), and real e2e (Docker + API key).
#
# Usage:
#   ./scripts/run_e2e.sh              # Run all available tiers
#   ./scripts/run_e2e.sh sqlite       # SQLite only (no Docker needed)
#   ./scripts/run_e2e.sh mock         # Mock e2e only (starts Docker)
#   ./scripts/run_e2e.sh real         # Real API only (starts Docker, needs ANTHROPIC_API_KEY)
#   ./scripts/run_e2e.sh sqlite mock  # Multiple tiers
#
# Extra pytest args after --:
#   ./scripts/run_e2e.sh sqlite -- -k "test_streaming" -vv
#
# Per-test timeouts: sqlite=60s, mock=120s, real=120s (pytest --timeout)
# Docker startup timeout: 60s

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

# Collect results
declare -A TIER_RESULTS

info()  { echo -e "${BLUE}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*"; }
header() { echo -e "\n${BOLD}═══ $* ═══${NC}"; }

# Parse arguments: tier names before --, pytest args after --
TIERS=()
PYTEST_EXTRA=()
saw_separator=false
for arg in "$@"; do
    if [[ "$arg" == "--" ]]; then
        saw_separator=true
        continue
    fi
    if $saw_separator; then
        PYTEST_EXTRA+=("$arg")
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

# --- Port helpers ---

_is_port_free() {
    local port="$1"
    if command -v ss &>/dev/null; then
        ! ss -tlnH "sport = :${port}" | grep -q .
    else
        ! (echo >/dev/tcp/localhost/"${port}") 2>/dev/null
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

# Cleanup on exit
cleanup() {
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
        "${PYTEST_EXTRA[@]}" \
    || exit_code=$?

    return "$exit_code"
}

# --- Tier: mock ---

run_mock() {
    header "Mock E2E Tests (Docker + mock Anthropic server)"

    if ! check_docker; then
        warn "Skipping mock tier: Docker not available"
        return 2
    fi

    # Find a free port for the mock Anthropic server
    local mock_port
    mock_port="$(_find_free_port 18888)"
    export MOCK_ANTHROPIC_PORT="$mock_port"
    info "Mock Anthropic server will use port $mock_port"

    # Generate a compose override pointing the gateway at the mock server
    local mock_override
    mock_override="$(mktemp /tmp/compose-mock-XXXXXX.yaml)"
    cat > "$mock_override" <<YAML
services:
  gateway:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - ANTHROPIC_BASE_URL=http://host.docker.internal:${mock_port}
      - ANTHROPIC_API_KEY=mock-key
YAML

    docker_up docker-compose.yaml "$mock_override"

    export E2E_GATEWAY_URL="http://localhost:${GATEWAY_PORT:-8000}"

    info "Running tests..."
    local exit_code=0
    uv run pytest \
        -m mock_e2e \
        tests/luthien_proxy/e2e_tests/ \
        -v --timeout=30 --no-cov \
        "${PYTEST_EXTRA[@]}" \
    || exit_code=$?

    rm -f "$mock_override"
    docker_down
    return "$exit_code"
}

# --- Tier: real ---

run_real() {
    header "Real E2E Tests (Docker + Anthropic API)"

    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        warn "Skipping real tier: ANTHROPIC_API_KEY not set"
        return 2
    fi

    if ! check_docker; then
        warn "Skipping real tier: Docker not available"
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
        "${PYTEST_EXTRA[@]}" \
    || exit_code=$?

    docker_down
    return "$exit_code"
}

# --- Main ---

echo -e "${BOLD}Luthien E2E Test Runner${NC}"
echo "Tiers: ${TIERS[*]}"
[[ ${#PYTEST_EXTRA[@]} -gt 0 ]] && echo "Extra pytest args: ${PYTEST_EXTRA[*]}"
echo ""

overall_exit=0

for tier in "${TIERS[@]}"; do
    exit_code=0
    "run_$tier" || exit_code=$?

    case $exit_code in
        0) TIER_RESULTS[$tier]="passed"; ok "$tier: all tests passed" ;;
        2) TIER_RESULTS[$tier]="skipped"; warn "$tier: skipped" ;;
        *) TIER_RESULTS[$tier]="failed"; fail "$tier: tests failed (exit $exit_code)"; overall_exit=1 ;;
    esac
done

# --- Summary ---

header "Results"
for tier in "${TIERS[@]}"; do
    result="${TIER_RESULTS[$tier]:-unknown}"
    case $result in
        passed)  ok   "$tier" ;;
        skipped) warn "$tier (skipped)" ;;
        failed)  fail "$tier" ;;
    esac
done

exit $overall_exit
