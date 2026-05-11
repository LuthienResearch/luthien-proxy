#!/usr/bin/env bash
# Source-able config loader. Sets defaults, loads nightly.env if present,
# exports everything the rest of the scripts need.
#
# Defines:
#   NIGHTLY_DIR            absolute path to scripts/nightly/
#   NIGHTLY_REPO_URL       upstream git URL
#   NIGHTLY_REPO_BRANCH    branch to track
#   NIGHTLY_STATE_DIR      root state dir
#   NIGHTLY_REPO_DIR       state_dir/repo
#   NIGHTLY_RUNS_DIR       state_dir/runs
#   NIGHTLY_PUBLIC_DIR     state_dir/public (or override)
#   NIGHTLY_RUN_DIR        state_dir/runs/<run_id>  (set by start_run)
#   NIGHTLY_RUN_ID         <YYYY-MM-DD-HHMM>
#   NIGHTLY_CHECKS         comma-separated checks
#   NIGHTLY_TIMEOUT_*      per-check timeouts
#   AUTOFIX_*              autofix toggles
#   NIGHTLY_RUN_RETENTION  prune horizon
#   NIGHTLY_WEBHOOK_URL    optional notify URL
#
# Idempotent: safe to source multiple times.

set -euo pipefail

NIGHTLY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export NIGHTLY_DIR

# Defaults --------------------------------------------------------------
: "${NIGHTLY_REPO_URL:=https://github.com/LuthienResearch/luthien-proxy.git}"
: "${NIGHTLY_REPO_BRANCH:=main}"
: "${NIGHTLY_STATE_DIR:=${HOME}/.luthien/nightly}"
: "${NIGHTLY_CHECKS:=dev_checks,e2e_sqlite,e2e_mock,doc_drift}"
: "${NIGHTLY_TIMEOUT_DEV_CHECKS:=1800}"
: "${NIGHTLY_TIMEOUT_E2E:=3600}"
: "${NIGHTLY_TIMEOUT_DOC_DRIFT:=900}"
: "${AUTOFIX_ENABLED:=false}"
: "${AUTOFIX_TIMEOUT:=1800}"
: "${AUTOFIX_MAX_BUDGET_USD:=5}"
: "${AUTOFIX_BRANCH_PREFIX:=nightly-fix}"
: "${NIGHTLY_RUN_RETENTION:=30}"
: "${NIGHTLY_WEBHOOK_URL:=}"

# Load user overrides ---------------------------------------------------
if [[ -f "${NIGHTLY_DIR}/nightly.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "${NIGHTLY_DIR}/nightly.env"; set +a
fi

# Derived paths ---------------------------------------------------------
NIGHTLY_REPO_DIR="${NIGHTLY_STATE_DIR}/repo"
NIGHTLY_RUNS_DIR="${NIGHTLY_STATE_DIR}/runs"
: "${NIGHTLY_PUBLIC_DIR:=${NIGHTLY_STATE_DIR}/public}"

export NIGHTLY_REPO_URL NIGHTLY_REPO_BRANCH NIGHTLY_STATE_DIR
export NIGHTLY_REPO_DIR NIGHTLY_RUNS_DIR NIGHTLY_PUBLIC_DIR
export NIGHTLY_CHECKS
export NIGHTLY_TIMEOUT_DEV_CHECKS NIGHTLY_TIMEOUT_E2E NIGHTLY_TIMEOUT_DOC_DRIFT
export AUTOFIX_ENABLED AUTOFIX_TIMEOUT AUTOFIX_MAX_BUDGET_USD AUTOFIX_BRANCH_PREFIX
export NIGHTLY_RUN_RETENTION NIGHTLY_WEBHOOK_URL

# Helpers ---------------------------------------------------------------

# Cross-platform timeout: macOS lacks `timeout` by default; gtimeout via
# coreutils provides it. Fall through to no-timeout if neither is present.
nightly_timeout() {
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
        echo "[nightly] WARN: no timeout binary; running without limit" >&2
        "$@"
    fi
}

# Append a key=value (JSON-encoded value) to results.json under .checks.<name>
# Usage: nightly_record_check <name> <status> <log_relpath> [extra_json]
# status: pass|fail|skip|error
nightly_record_check() {
    local name="$1" status="$2" log="$3" extra="${4:-{}}"
    local results="${NIGHTLY_RUN_DIR}/results.json"
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

# Initialize a new run directory and results.json. Sets NIGHTLY_RUN_ID and
# NIGHTLY_RUN_DIR.
nightly_start_run() {
    # Second-resolution ID avoids collisions when two runs land in the
    # same minute (manual run during scheduled fire, retry-on-failure,
    # `--once` invocations).
    NIGHTLY_RUN_ID="$(date +%Y-%m-%d-%H%M%S)"
    NIGHTLY_RUN_DIR="${NIGHTLY_RUNS_DIR}/${NIGHTLY_RUN_ID}"
    export NIGHTLY_RUN_ID NIGHTLY_RUN_DIR
    mkdir -p "${NIGHTLY_RUN_DIR}"
    python3 - "$NIGHTLY_RUN_DIR" "$NIGHTLY_RUN_ID" <<'PY'
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

nightly_finish_run() {
    python3 - "$NIGHTLY_RUN_DIR" <<'PY'
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
