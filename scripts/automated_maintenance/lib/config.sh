#!/usr/bin/env bash
# Source-able config loader. Sets defaults, loads automated_maintenance.env if present,
# exports everything the rest of the scripts need.
#
# Defines:
#   MAINT_DIR            absolute path to scripts/automated_maintenance/
#   MAINT_REPO_URL       upstream git URL
#   MAINT_REPO_BRANCH    branch to track
#   MAINT_STATE_DIR      root state dir
#   MAINT_REPO_DIR       state_dir/repo
#   MAINT_RUNS_DIR       state_dir/runs
#   MAINT_PUBLIC_DIR     state_dir/public (or override)
#   MAINT_RUN_DIR        state_dir/runs/<run_id>  (set by start_run)
#   MAINT_RUN_ID         <YYYY-MM-DD-HHMM>
#   MAINT_CHECKS         comma-separated checks
#   MAINT_TIMEOUT_*      per-check timeouts
#   AUTOFIX_*              autofix toggles
#   MAINT_RUN_RETENTION  prune horizon
#   MAINT_WEBHOOK_URL    optional notify URL
#
# Idempotent: safe to source multiple times.

set -euo pipefail

MAINT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MAINT_DIR

# Defaults --------------------------------------------------------------
: "${MAINT_REPO_URL:=https://github.com/LuthienResearch/luthien-proxy.git}"
: "${MAINT_REPO_BRANCH:=main}"
: "${MAINT_STATE_DIR:=${HOME}/.luthien/automated_maintenance}"
: "${MAINT_CHECKS:=dev_checks,e2e_sqlite,e2e_mock,doc_drift}"
: "${MAINT_TIMEOUT_DEV_CHECKS:=1800}"
: "${MAINT_TIMEOUT_E2E:=3600}"
: "${MAINT_TIMEOUT_DOC_DRIFT:=900}"
: "${AUTOFIX_ENABLED:=false}"
: "${AUTOFIX_TIMEOUT:=1800}"
: "${AUTOFIX_MAX_BUDGET_USD:=5}"
: "${AUTOFIX_BRANCH_PREFIX:=maint-fix}"
: "${MAINT_RUN_RETENTION:=30}"
: "${MAINT_WEBHOOK_URL:=}"

# Load user overrides ---------------------------------------------------
if [[ -f "${MAINT_DIR}/automated_maintenance.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${MAINT_DIR}/automated_maintenance.env"
    set +a
fi

# Reject AUTOFIX_BRANCH_PREFIX values that could contain shell or glob
# metachars — the prefix is interpolated into `git for-each-ref` patterns
# and `gh pr list --search head:...` queries downstream. The default
# `maint-fix` is fine; only an operator override could trigger this.
if [[ ! "${AUTOFIX_BRANCH_PREFIX}" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    echo "[maint] FATAL: AUTOFIX_BRANCH_PREFIX (${AUTOFIX_BRANCH_PREFIX}) must match" >&2
    echo "        ^[A-Za-z0-9._/-]+\$ — got something containing shell metachars" >&2
    exit 2
fi

# Derived paths ---------------------------------------------------------
MAINT_REPO_DIR="${MAINT_STATE_DIR}/repo"
MAINT_RUNS_DIR="${MAINT_STATE_DIR}/runs"
: "${MAINT_PUBLIC_DIR:=${MAINT_STATE_DIR}/public}"

export MAINT_REPO_URL MAINT_REPO_BRANCH MAINT_STATE_DIR
export MAINT_REPO_DIR MAINT_RUNS_DIR MAINT_PUBLIC_DIR
export MAINT_CHECKS
export MAINT_TIMEOUT_DEV_CHECKS MAINT_TIMEOUT_E2E MAINT_TIMEOUT_DOC_DRIFT
export AUTOFIX_ENABLED AUTOFIX_TIMEOUT AUTOFIX_MAX_BUDGET_USD AUTOFIX_BRANCH_PREFIX
export MAINT_RUN_RETENTION MAINT_WEBHOOK_URL

# Helpers ---------------------------------------------------------------

# Cross-platform timeout: macOS lacks `timeout` by default; gtimeout via
# coreutils provides it. A scheduled job MUST have a timeout — otherwise
# a hung check could hold the lock and block every subsequent fire.
# `check_prereqs` in automated_maintenance.sh calls maint_have_timeout
# at startup so a misconfigured install fails loudly, not silently wedged.
maint_have_timeout() {
    command -v timeout >/dev/null 2>&1 || command -v gtimeout >/dev/null 2>&1
}

maint_timeout() {
    local secs="$1"; shift
    if [[ "${secs}" -eq 0 ]]; then
        "$@"
        return $?
    fi
    if command -v timeout >/dev/null 2>&1; then
        timeout "${secs}" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout "${secs}" "$@"
    else
        echo "[maint] FATAL: no timeout binary (expected one of: timeout, gtimeout)" >&2
        exit 2
    fi
}

# Append a key=value (JSON-encoded value) to results.json under .checks.<name>
# Usage: maint_record_check <name> <status> <log_relpath> [extra_json]
# status: pass|fail|skip|error
maint_record_check() {
    # NOTE: do not collapse the `extra` default into `${4:-{}}` — bash parses
    # that as `${4:-{}` plus a literal `}`, appending a stray brace to any
    # value (every value passed here ends in `}`). That made `json.loads(extra)`
    # below fail with "Extra data" and silently dropped every check from
    # results.json (checks: {}, overall: unknown).
    local name="$1" status="$2" log="$3"
    local extra="${4:-}"
    [[ -n "${extra}" ]] || extra='{}'
    local results="${MAINT_RUN_DIR}/results.json"
    python3 - "$results" "$name" "$status" "$log" "$extra" <<'PY'
import json, sys, pathlib
results_path, name, status, log, extra = sys.argv[1:6]
p = pathlib.Path(results_path)
data = json.loads(p.read_text()) if p.exists() else {"checks": {}}
data.setdefault("checks", {})
data["checks"][name] = {
    "status": status,
    "log": log,
    **json.loads(extra),
}
p.write_text(json.dumps(data, indent=2) + "\n")
PY
}

# Initialize a new run directory and results.json. Sets MAINT_RUN_ID and
# MAINT_RUN_DIR.
# Args:
#   $1: optional suffix (e.g. "once") to tag debug runs so the dashboard
#       can distinguish them from real scheduled runs.
maint_start_run() {
    local suffix="${1:-}"
    # Second-resolution ID avoids collisions when two runs land in the
    # same minute (manual run during scheduled fire, retry-on-failure,
    # `--once` invocations).
    MAINT_RUN_ID="$(date +%Y-%m-%d-%H%M%S)"
    [[ -n "${suffix}" ]] && MAINT_RUN_ID="${MAINT_RUN_ID}-${suffix}"
    MAINT_RUN_DIR="${MAINT_RUNS_DIR}/${MAINT_RUN_ID}"
    export MAINT_RUN_ID MAINT_RUN_DIR
    mkdir -p "${MAINT_RUN_DIR}"
    python3 - "$MAINT_RUN_DIR" "$MAINT_RUN_ID" <<'PY'
import json, os, pathlib, sys, datetime
run_dir, run_id = sys.argv[1:3]
data = {
    "run_id": run_id,
    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "host": os.uname().nodename,
    "checks": {},
    "autofix": None,
}
pathlib.Path(run_dir, "results.json").write_text(json.dumps(data, indent=2) + "\n")
PY
}

maint_finish_run() {
    python3 - "$MAINT_RUN_DIR" <<'PY'
import json, pathlib, sys, datetime
p = pathlib.Path(sys.argv[1], "results.json")
data = json.loads(p.read_text())
data["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
checks = data.get("checks", {})
statuses = {c.get("status") for c in checks.values()}
if "fail" in statuses or "error" in statuses:
    overall = "fail"
elif statuses and statuses <= {"pass", "skip"}:
    overall = "pass"
else:
    overall = "unknown"
data["overall"] = overall
p.write_text(json.dumps(data, indent=2) + "\n")
PY
}
