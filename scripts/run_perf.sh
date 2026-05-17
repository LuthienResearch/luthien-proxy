#!/usr/bin/env bash
# Playwright version: 1.50.0
#
# ISOLATION ENFORCEMENT:
#   This script refuses to run against the development database (~/.luthien/local.db).
#   Perf tests use a dedicated isolated database to prevent fixture data pollution
#   and ensure reproducible baseline measurements:
#     SQLite:   ~/.luthien/perf.db  (hardcoded; never local.db)
#     Postgres: perf_test schema in a dedicated Postgres perf instance
#   DATABASE_URL must be set explicitly and must not reference local.db.
#
# ABOUTME: Performance test runner for admin UI latency and payload SLOs.

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

info()   { echo -e "${BLUE}▸${NC} $*"; }
ok()     { echo -e "${GREEN}✓${NC} $*"; }
warn()   { echo -e "${YELLOW}⚠${NC} $*"; }
fail()   { echo -e "${RED}✗${NC} $*"; }
header() { echo -e "\n${BOLD}═══ $* ═══${NC}"; }

# ── Defaults ──────────────────────────────────────────────────────────────────

TIER=""
FIXTURE="sami-like"
SEED_ONLY=false
CLEAN=false
ASSERT_SLO=false
THROTTLED=false
BACKEND="sqlite"

# ── Help ──────────────────────────────────────────────────────────────────────

show_help() {
    cat <<'EOF'
Performance test runner for admin UI latency and payload SLOs.

Usage:
  ./scripts/run_perf.sh --tier {100|1000|10000} [options]
  ./scripts/run_perf.sh --clean [--backend {sqlite|postgres}]
  ./scripts/run_perf.sh --help

Options:
  --tier {100|1000|10000}      Sessions to seed [required unless --clean]
  --fixture {sami-like}        Fixture profile (default: sami-like)
  --seed-only                  Seed the database; skip test assertions
  --clean                      Drop the perf database and exit
  --assert-slo                 Fail if any SLO thresholds are exceeded (sets PERF_ASSERT_SLO=1)
  --throttled                  CDP network throttling -- 1 Mbps + 300ms RTT (only with --fixture sami-like)
  --backend {sqlite|postgres}  Database backend (default: sqlite)
  --help                       Show this help message

Environment:
  DATABASE_URL   Required (refused if unset or contains local.db)
                 SQLite example: sqlite:///$HOME/.luthien/perf.db

Examples:
  DATABASE_URL=sqlite:///$HOME/.luthien/perf.db ./scripts/run_perf.sh --tier 100
  DATABASE_URL=sqlite:///$HOME/.luthien/perf.db ./scripts/run_perf.sh --tier 100 --assert-slo
  DATABASE_URL=sqlite:///$HOME/.luthien/perf.db ./scripts/run_perf.sh --tier 100 --throttled
  ./scripts/run_perf.sh --clean
  DATABASE_URL=sqlite:///$HOME/.luthien/perf.db ./scripts/run_perf.sh --seed-only --tier 1000

Postgres --clean note:
  For Postgres, --clean executes DROP SCHEMA perf_test CASCADE.
  Set DATABASE_URL to the Postgres perf instance before running.
EOF
    exit 0
}

# ── Argument parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tier)
            if [[ $# -lt 2 ]]; then fail "--tier requires an argument"; exit 1; fi
            case "$2" in
                100|1000|10000) TIER="$2" ;;
                *) fail "Invalid --tier: $2 (expected: 100, 1000, or 10000)"; exit 1 ;;
            esac
            shift 2
            ;;
        --fixture)
            if [[ $# -lt 2 ]]; then fail "--fixture requires an argument"; exit 1; fi
            case "$2" in
                sami-like) FIXTURE="$2" ;;
                *) fail "Unknown --fixture: $2 (expected: sami-like)"; exit 1 ;;
            esac
            shift 2
            ;;
        --backend)
            if [[ $# -lt 2 ]]; then fail "--backend requires an argument"; exit 1; fi
            case "$2" in
                sqlite|postgres) BACKEND="$2" ;;
                *) fail "Unknown --backend: $2 (expected: sqlite or postgres)"; exit 1 ;;
            esac
            shift 2
            ;;
        --seed-only)  SEED_ONLY=true; shift ;;
        --clean)      CLEAN=true; shift ;;
        --assert-slo) ASSERT_SLO=true; shift ;;
        --throttled)  THROTTLED=true; shift ;;
        --help|-h)    show_help ;;
        *)            fail "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Validate option combinations ──────────────────────────────────────────────

if $THROTTLED && [[ "$FIXTURE" != "sami-like" ]]; then
    fail "--throttled is only valid with --fixture sami-like (got: --fixture $FIXTURE)"
    exit 1
fi

if ! $CLEAN && [[ -z "$TIER" ]]; then
    fail "Required: --tier {100|1000|10000}  (or use --clean to drop the perf DB)"
    exit 1
fi

# ── SQLite clean ──────────────────────────────────────────────────────────────
# Runs before the isolation check: deletes the perf DB file, never the dev DB.

if $CLEAN && [[ "$BACKEND" == "sqlite" ]]; then
    header "Cleaning Perf Database (SQLite)"
    PERF_DB="$HOME/.luthien/perf.db"
    if [[ -f "$PERF_DB" ]]; then
        rm -f "$PERF_DB"
        ok "Removed $PERF_DB"
    else
        info "Nothing to clean: $PERF_DB does not exist"
    fi
    exit 0
fi

# ── Isolation check ───────────────────────────────────────────────────────────
#
# This script refuses to run against the dev database. Perf tests MUST use an
# isolated database to prevent fixture data pollution and ensure reproducibility.
# Applies to all non-SQLite-clean operations.

_db_url="${DATABASE_URL:-}"

if [[ -z "$_db_url" ]]; then
    fail "ISOLATION REFUSED: DATABASE_URL is not set."
    fail "  The gateway defaults to ~/.luthien/local.db (the dev database) when unset."
    fail "  This script refuses to run without an explicit isolated database URL."
    fail "  Set DATABASE_URL to a perf-specific path, e.g.:"
    fail "    export DATABASE_URL=sqlite:///\$HOME/.luthien/perf.db"
    exit 1
fi

if [[ "$_db_url" == *"local.db"* ]]; then
    fail "ISOLATION REFUSED: DATABASE_URL points to the dev database (local.db)."
    fail "  This script refuses to run against local.db to prevent data pollution."
    fail "  DATABASE_URL=$_db_url"
    fail "  Set DATABASE_URL to a perf-specific path, e.g.:"
    fail "    export DATABASE_URL=sqlite:///\$HOME/.luthien/perf.db"
    exit 1
fi

# ── Postgres clean (after isolation check) ────────────────────────────────────

if $CLEAN && [[ "$BACKEND" == "postgres" ]]; then
    header "Cleaning Perf Database (Postgres)"
    warn "Executing: DROP SCHEMA perf_test CASCADE"
    warn "  Target: $_db_url"
    uv run python - <<'PYEOF'
import os
import sys

try:
    import psycopg2  # type: ignore[import-untyped]
except ImportError:
    print("psycopg2 not installed; run: uv add psycopg2-binary", file=sys.stderr)
    sys.exit(1)

try:
    url = os.environ["DATABASE_URL"]
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS perf_test CASCADE")
    conn.close()
    print("perf_test schema dropped")
except Exception as exc:
    print(f"Error dropping schema: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
    ok "Postgres perf_test schema dropped"
    exit 0
fi

# ── Tier-10000 disk warning ───────────────────────────────────────────────────

if [[ "$TIER" == "10000" ]]; then
    warn "Tier-10000 seeds ~250k events × ~25 KB payload ≈ 7–8 GB on disk. Ensure sufficient free space."
    warn "Seeding also allocates a 128 MB SQLite cache; ensure sufficient RAM."
fi

# ── Pre-flight ────────────────────────────────────────────────────────────────

header "Pre-flight Checks"

# Ensure Chromium is installed (Playwright 1.50.0 -- pinned at top of file).
info "Checking Playwright Chromium..."
uv run playwright install chromium --with-deps 2>/dev/null || true

_chromium_ver="$(uv run python -c '
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    ver = browser.version
    browser.close()
    print(ver)
' 2>/dev/null || echo "unknown")"

_git_sha="$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")"

ok "Chromium version: $_chromium_ver"
ok "Git SHA:          $_git_sha"

# ── Environment ───────────────────────────────────────────────────────────────

export PERF_TIER="$TIER"
export PERF_FIXTURE="$FIXTURE"
export PERF_BACKEND="$BACKEND"

if $ASSERT_SLO; then
    export PERF_ASSERT_SLO=1
    export PERF_THROTTLE_BASELINE=1
    export PERF_ASSERT_MEMORY=1
    info "SLO assertion enabled -- tests fail if thresholds exceeded"
fi

if $THROTTLED; then
    export PERF_THROTTLED=1
    info "Network throttling enabled -- 1 Mbps bandwidth + 300ms RTT (sami-like profile)"
fi

# ── Seed only ─────────────────────────────────────────────────────────────────

if $SEED_ONLY; then
    header "Seeding Database (tier=$TIER, fixture=$FIXTURE)"
    info "Seeding $TIER sessions -- test assertions will NOT run"
    export PERF_SEED_ONLY=1
    uv run pytest \
        -m perf \
        tests/luthien_proxy/perf_tests/ \
        -v --no-cov \
        || true
    ok "Seeding complete"
    exit 0
fi

# ── Run perf tests ────────────────────────────────────────────────────────────

_slo_flag="no"
_throttle_flag="no"
$ASSERT_SLO && _slo_flag="yes"
$THROTTLED && _throttle_flag="yes"

header "Perf Tests"
info "  Tier:        $TIER sessions"
info "  Fixture:     $FIXTURE"
info "  Backend:     $BACKEND"
info "  Assert SLO:  $_slo_flag"
info "  Throttled:   $_throttle_flag"
info "  Database:    $_db_url"

exit_code=0
uv run pytest \
    -m perf \
    tests/luthien_proxy/perf_tests/ \
    -v --no-cov \
    || exit_code=$?

# ── Summary ───────────────────────────────────────────────────────────────────

header "Results"
if [[ $exit_code -eq 0 ]]; then
    ok "All perf tests passed"
    $ASSERT_SLO && ok "SLO thresholds: all met"
else
    fail "Perf tests failed (exit $exit_code)"
    $ASSERT_SLO && fail "One or more SLO thresholds were exceeded"
fi

exit $exit_code
