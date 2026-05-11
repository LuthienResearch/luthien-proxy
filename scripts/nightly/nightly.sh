#!/usr/bin/env bash
# Nightly maintenance for luthien-proxy.
#
# Update repo → run checks → optional autofix → render dashboard → tear down.
# Designed to run from a scheduler (launchd on macOS, systemd timer on Linux,
# or plain cron). Idempotent and safe to invoke at any time.
#
# Usage:
#   nightly.sh                  # default config
#   nightly.sh --once <check>   # run a single check (debugging)

set -euo pipefail

NIGHTLY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/config.sh
source "${NIGHTLY_DIR}/lib/config.sh"
# shellcheck source=lib/checks.sh
source "${NIGHTLY_DIR}/lib/checks.sh"
# shellcheck source=lib/doc_drift.sh
source "${NIGHTLY_DIR}/lib/doc_drift.sh"
# shellcheck source=lib/autofix.sh
source "${NIGHTLY_DIR}/lib/autofix.sh"

log() { echo "[nightly] $*" >&2; }

check_prereqs() {
    local missing=()
    for bin in git python3; do
        command -v "${bin}" >/dev/null 2>&1 || missing+=("${bin}")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log "FATAL: missing required binaries: ${missing[*]}"
        exit 2
    fi
}

#
# Refresh the state-dir clone. The default behavior wipes local changes
# so the run starts from a known origin SHA. Pass `preserve=1` to skip
# the destructive reset — used by --once so developers can iterate on
# the clone without losing their edits between debug runs.
ensure_repo() {
    local preserve="${1:-0}"
    if [[ ! -d "${NIGHTLY_REPO_DIR}/.git" ]]; then
        log "cloning ${NIGHTLY_REPO_URL} → ${NIGHTLY_REPO_DIR}"
        mkdir -p "$(dirname "${NIGHTLY_REPO_DIR}")"
        git clone --branch "${NIGHTLY_REPO_BRANCH}" "${NIGHTLY_REPO_URL}" "${NIGHTLY_REPO_DIR}"
    elif [[ "${preserve}" == "1" ]]; then
        log "preserving local state in ${NIGHTLY_REPO_DIR} (--once)"
    else
        # Defensive: refuse to reset --hard a checkout whose origin doesn't
        # match NIGHTLY_REPO_URL. Protects against a sloppy env override
        # pointing at a real working clone.
        local actual_url
        actual_url="$(git -C "${NIGHTLY_REPO_DIR}" remote get-url origin 2>/dev/null || true)"
        if [[ "${actual_url}" != "${NIGHTLY_REPO_URL}" ]]; then
            log "FATAL: ${NIGHTLY_REPO_DIR} origin (${actual_url}) does not match"
            log "       NIGHTLY_REPO_URL (${NIGHTLY_REPO_URL}). Refusing to reset --hard."
            log "       If this is the intended state-dir clone, delete it and re-run."
            exit 3
        fi
        log "updating ${NIGHTLY_REPO_DIR}"
        git -C "${NIGHTLY_REPO_DIR}" fetch --prune origin
        # Reset hard to origin to wipe any stale autofix branch state.
        git -C "${NIGHTLY_REPO_DIR}" checkout -B "${NIGHTLY_REPO_BRANCH}" "origin/${NIGHTLY_REPO_BRANCH}"
        git -C "${NIGHTLY_REPO_DIR}" reset --hard "origin/${NIGHTLY_REPO_BRANCH}"
        git -C "${NIGHTLY_REPO_DIR}" clean -fdx
    fi
    # Record the SHA we're testing.
    local sha
    sha="$(git -C "${NIGHTLY_REPO_DIR}" rev-parse HEAD)"
    python3 - "$NIGHTLY_RUN_DIR" "$sha" <<'PY'
import json, pathlib, sys
run_dir, sha = sys.argv[1:3]
p = pathlib.Path(run_dir, "results.json")
data = json.loads(p.read_text())
data["sha"] = sha
p.write_text(json.dumps(data, indent=2) + "\n")
PY
}

teardown() {
    log "teardown"
    # Best-effort: tear down any docker compose stack the e2e tier brought up.
    # `-v --remove-orphans` is destructive, which is OK because the compose
    # project name comes from `basename "$NIGHTLY_REPO_DIR"` (= "repo"). It
    # won't collide with a dev checkout's stack unless that dev clone is
    # also literally named `repo`.
    if [[ -f "${NIGHTLY_REPO_DIR}/docker-compose.yaml" ]] && command -v docker >/dev/null 2>&1; then
        ( cd "${NIGHTLY_REPO_DIR}" && docker compose down -v --remove-orphans ) >/dev/null 2>&1 || true
    fi
    # Release the concurrency lock if we acquired one.
    if [[ -n "${NIGHTLY_LOCK:-}" ]]; then
        rmdir "${NIGHTLY_LOCK}" 2>/dev/null || true
    fi
}

rotate_scheduler_logs() {
    # The scheduler (launchd/systemd) writes stdout/stderr to fixed paths
    # in append mode. Without rotation these grow forever.
    #
    # Caveat: rotation happens AT THE START of each nightly run. The
    # *previous* run has by then exited and the scheduler has closed
    # its fd, so the mv is safe. But while THIS run is in flight the
    # scheduler still holds an fd on `nightly.out.log` from before
    # rotation, so output from the current run continues to land in the
    # rotated file (e.g. `.1`) rather than a fresh one. The next run
    # picks up the fresh file. This is fine for size-bound rotation —
    # we only care that no single file grows unbounded across runs.
    local log_dir="${NIGHTLY_STATE_DIR}/logs"
    local keep="${NIGHTLY_RUN_RETENTION:-30}"
    [[ -d "${log_dir}" ]] || return 0
    for base in nightly.out.log nightly.err.log; do
        local path="${log_dir}/${base}"
        [[ -f "${path}" ]] || continue
        # Skip if the file is small (a few KB); rotation is for runs
        # that produce real volume.
        if [[ "$(wc -c <"${path}")" -lt 1048576 ]]; then
            continue
        fi
        # Drop the oldest, then shift the rest: .N-1→.N, ..., .1→.2.
        rm -f "${path}.${keep}"
        for ((i = keep - 1; i >= 1; i--)); do
            [[ -f "${path}.${i}" ]] && mv "${path}.${i}" "${path}.$((i + 1))"
        done
        mv "${path}" "${path}.1"
    done
}

notify() {
    local url="${NIGHTLY_WEBHOOK_URL}"
    [[ -z "${url}" ]] && return 0
    local payload
    # Build the JSON payload in Python so values are properly escaped —
    # raw shell interpolation would break on any value containing `"`.
    payload="$(python3 - "${NIGHTLY_RUN_DIR}/results.json" "${NIGHTLY_RUN_ID}" <<'PY'
import json, sys
results_path, run_id = sys.argv[1:3]
r = json.load(open(results_path))
checks = r.get("checks", {})
parts = [f"{n}={c.get('status','?')}" for n, c in checks.items()]
af = r.get("autofix") or {}
if af:
    parts.append(f"autofix={af.get('status','?')}")
summary = " ".join(parts)
overall = r.get("overall", "?")
print(json.dumps({"text": f"nightly {run_id} {overall}: {summary}"}))
PY
)"
    curl -sfS -X POST -H "Content-Type: application/json" \
        -d "${payload}" \
        "${url}" >/dev/null 2>&1 || log "notify failed"
}

run_one_check() {
    case "$1" in
        doc_drift) nightly_run_doc_drift ;;
        e2e_real) nightly_run_e2e_real ;;
        *) nightly_run_check "$1" ;;
    esac
}

main() {
    trap teardown EXIT
    check_prereqs
    mkdir -p "${NIGHTLY_RUNS_DIR}" "${NIGHTLY_PUBLIC_DIR}"

    # Concurrency guard: refuse to start if another nightly run is in flight.
    # Two simultaneous runs would race on `git fetch && reset --hard && clean`
    # inside NIGHTLY_REPO_DIR. Lock via `mkdir` (atomic on POSIX); cleaned up
    # in `teardown`.
    local lock="${NIGHTLY_STATE_DIR}/.lock"
    if ! mkdir "${lock}" 2>/dev/null; then
        log "another nightly run holds ${lock} — exiting"
        exit 4
    fi
    NIGHTLY_LOCK="${lock}"
    export NIGHTLY_LOCK

    rotate_scheduler_logs
    nightly_start_run
    log "run ${NIGHTLY_RUN_ID} → ${NIGHTLY_RUN_DIR}"

    ensure_repo

    IFS=',' read -r -a checks <<< "${NIGHTLY_CHECKS}"
    for check in "${checks[@]}"; do
        check="$(echo "${check}" | tr -d '[:space:]')"
        [[ -z "${check}" ]] && continue
        run_one_check "${check}" || true   # never abort the loop on a single failure
    done

    nightly_run_autofix || true

    nightly_finish_run

    log "rendering dashboard → ${NIGHTLY_PUBLIC_DIR}"
    python3 "${NIGHTLY_DIR}/lib/dashboard.py" \
        --runs-dir "${NIGHTLY_RUNS_DIR}" \
        --public-dir "${NIGHTLY_PUBLIC_DIR}" \
        --retention "${NIGHTLY_RUN_RETENTION}"

    notify
    log "done"
}

# --once mode: run a single check against the existing clone, skip the
# autofix/dashboard pipeline. Used for debugging — local edits in the
# state-dir clone are preserved between invocations so you can iterate.
if [[ "${1:-}" == "--once" ]]; then
    check_prereqs
    # Tag the run ID with `-once` so the dashboard can distinguish a
    # debug run (single check, no autofix) from a real scheduled run.
    nightly_start_run once
    ensure_repo 1   # preserve=1 — don't reset the clone
    run_one_check "${2:?usage: nightly.sh --once <check>}"
    nightly_finish_run
    exit 0
fi

main "$@"
