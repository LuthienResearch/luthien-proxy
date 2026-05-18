#!/usr/bin/env bash
# Automated maintenance for luthien-proxy.
#
# Update repo → run checks → optional autofix → render dashboard → tear down.
# Designed to run from a scheduler (launchd on macOS, systemd timer on Linux,
# or plain cron). Idempotent and safe to invoke at any time.
#
# Usage:
#   automated_maintenance.sh                  # default config
#   automated_maintenance.sh --once <check>   # run a single check (debugging)

set -euo pipefail

MAINT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/config.sh
source "${MAINT_DIR}/lib/config.sh"
# shellcheck source=lib/checks.sh
source "${MAINT_DIR}/lib/checks.sh"
# shellcheck source=lib/doc_drift.sh
source "${MAINT_DIR}/lib/doc_drift.sh"
# shellcheck source=lib/autofix.sh
source "${MAINT_DIR}/lib/autofix.sh"

log() { echo "[maint] $*" >&2; }

check_prereqs() {
    local missing=()
    for bin in git python3; do
        command -v "${bin}" >/dev/null 2>&1 || missing+=("${bin}")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log "FATAL: missing required binaries: ${missing[*]}"
        exit 2
    fi
    # Fail loudly at startup, not mid-run: a scheduled job without a
    # working `timeout` would silently run unbounded and risk wedging
    # the lock indefinitely.
    if ! maint_have_timeout; then
        log "FATAL: no timeout binary on PATH (need one of: timeout, gtimeout)"
        log "       macOS: brew install coreutils  |  Linux: usually preinstalled"
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
    if [[ ! -d "${MAINT_REPO_DIR}/.git" ]]; then
        log "cloning ${MAINT_REPO_URL} → ${MAINT_REPO_DIR}"
        mkdir -p "$(dirname "${MAINT_REPO_DIR}")"
        git clone --branch "${MAINT_REPO_BRANCH}" "${MAINT_REPO_URL}" "${MAINT_REPO_DIR}"
    elif [[ "${preserve}" == "1" ]]; then
        log "preserving local state in ${MAINT_REPO_DIR} (--once)"
    else
        # Defensive: refuse to reset --hard a checkout whose origin doesn't
        # match MAINT_REPO_URL. Protects against a sloppy env override
        # pointing at a real working clone.
        local actual_url
        actual_url="$(git -C "${MAINT_REPO_DIR}" remote get-url origin 2>/dev/null || true)"
        if [[ "${actual_url}" != "${MAINT_REPO_URL}" ]]; then
            log "FATAL: ${MAINT_REPO_DIR} origin (${actual_url}) does not match"
            log "       MAINT_REPO_URL (${MAINT_REPO_URL}). Refusing to reset --hard."
            log "       If this is the intended state-dir clone, delete it and re-run."
            exit 3
        fi
        log "updating ${MAINT_REPO_DIR}"
        git -C "${MAINT_REPO_DIR}" fetch --prune origin
        # Reset hard to origin to wipe any stale autofix branch state.
        git -C "${MAINT_REPO_DIR}" checkout -B "${MAINT_REPO_BRANCH}" "origin/${MAINT_REPO_BRANCH}"
        git -C "${MAINT_REPO_DIR}" reset --hard "origin/${MAINT_REPO_BRANCH}"
        git -C "${MAINT_REPO_DIR}" clean -fdx
        # Delete local autofix branches from prior runs. They're pushed to
        # origin and lived only here; without this loop they accumulate
        # indefinitely (one per failed autofix attempt).
        local stale_branches
        stale_branches="$(git -C "${MAINT_REPO_DIR}" for-each-ref \
            --format='%(refname:short)' \
            "refs/heads/${AUTOFIX_BRANCH_PREFIX}/" 2>/dev/null || true)"
        if [[ -n "${stale_branches}" ]]; then
            while IFS= read -r br; do
                [[ -n "${br}" ]] && git -C "${MAINT_REPO_DIR}" branch -D "${br}" >/dev/null
            done <<< "${stale_branches}"
        fi
        # Delete remote autofix branches whose PRs are closed or merged.
        # We never delete branches with open PRs (the operator may still
        # be reviewing them). Requires `gh` to be authenticated; silent
        # no-op otherwise so a missing `gh` doesn't break ensure_repo.
        if command -v gh >/dev/null 2>&1; then
            local closed_branches
            # Explicit --repo: don't rely on cwd. `gh` defaults to the
            # current directory's repo but a future caller might invoke
            # this from elsewhere and silently target the wrong repo.
            closed_branches="$(gh pr list \
                --repo "$(git -C "${MAINT_REPO_DIR}" remote get-url origin)" \
                --state closed \
                --search "head:${AUTOFIX_BRANCH_PREFIX}/" \
                --json headRefName \
                --jq '.[].headRefName' 2>/dev/null || true)"
            if [[ -n "${closed_branches}" ]]; then
                while IFS= read -r br; do
                    if [[ -n "${br}" ]]; then
                        git -C "${MAINT_REPO_DIR}" push origin --delete "${br}" >/dev/null 2>&1 || true
                    fi
                done <<< "${closed_branches}"
            fi
        fi
    fi
    # Record the SHA we're testing.
    local sha
    sha="$(git -C "${MAINT_REPO_DIR}" rev-parse HEAD)"
    python3 - "$MAINT_RUN_DIR" "$sha" <<'PY'
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
    # In `--once` (debug) mode we leave the docker stack alone — the
    # whole point is to iterate without losing state between runs.
    # MAINT_ONCE is set in the --once branch below.
    if [[ "${MAINT_ONCE:-0}" != "1" ]]; then
        # Best-effort: tear down any docker compose stack the e2e tier
        # brought up. `-v --remove-orphans` is destructive — only run it
        # if MAINT_REPO_DIR is genuinely inside MAINT_STATE_DIR.
        # Otherwise a misconfigured env pointing the clone at a real
        # dev checkout would nuke that dev stack's volumes. The compose
        # project name defaults to `basename "$MAINT_REPO_DIR"` so we'd
        # otherwise hit any project sharing that basename.
        if [[ "${MAINT_REPO_DIR}" == "${MAINT_STATE_DIR}/"* ]] && \
           [[ -f "${MAINT_REPO_DIR}/docker-compose.yaml" ]] && \
           command -v docker >/dev/null 2>&1; then
            ( cd "${MAINT_REPO_DIR}" && docker compose down -v --remove-orphans ) >/dev/null 2>&1 || true
        fi
    fi
    # Release the concurrency lock if we acquired one.
    if [[ -n "${MAINT_LOCK:-}" ]]; then
        rm -rf "${MAINT_LOCK}" 2>/dev/null || true
    fi
}

rotate_scheduler_logs() {
    # The scheduler (launchd/systemd) writes stdout/stderr to fixed paths
    # in append mode. Without rotation these grow forever.
    #
    # Caveat: rotation runs at the START of each maintenance run, but
    # the scheduler may keep an fd open on the original path across
    # runs (launchd in particular tends to). So when we `mv` the file
    # mid-run, the current run's output continues landing in the *moved*
    # file (e.g. `.1`) via the still-open fd, and a fresh file isn't
    # created until the scheduler closes and reopens — which may not
    # happen until the unit reloads. This is acceptable for size-bound
    # rotation: no single file grows unbounded across cycles. For real
    # log-rotation discipline, point launchd/systemd at logger/rsyslog
    # or use logrotate with `copytruncate`.
    local log_dir="${MAINT_STATE_DIR}/logs"
    local keep="${MAINT_RUN_RETENTION:-30}"
    [[ -d "${log_dir}" ]] || return 0
    for base in maintenance.out.log maintenance.err.log; do
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
    local url="${MAINT_WEBHOOK_URL}"
    [[ -z "${url}" ]] && return 0
    local payload
    # Build the JSON payload in Python so values are properly escaped —
    # raw shell interpolation would break on any value containing `"`.
    payload="$(python3 - "${MAINT_RUN_DIR}/results.json" "${MAINT_RUN_ID}" <<'PY'
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
print(json.dumps({"text": f"maintenance {run_id} {overall}: {summary}"}))
PY
)"
    curl -sfS -X POST -H "Content-Type: application/json" \
        -d "${payload}" \
        "${url}" >/dev/null 2>&1 || log "notify failed"
}

run_one_check() {
    case "$1" in
        doc_drift) maint_run_doc_drift ;;
        e2e_real) maint_run_e2e_real ;;
        *) maint_run_check "$1" ;;
    esac
}

# Acquire the concurrency lock. Refuses to start if another maintenance run
# is in flight; reclaims the lock if the recorded PID is no longer alive
# (crashed run, reboot, OOM, SIGKILL).
#
# Reclaim race: two processes both seeing the same stale lock cannot both
# successfully claim it, because the reclaim uses an atomic directory
# `mv` (rename) as the contention-resolution step. Whoever wins the
# rename "stashed" the stale dir under a PID-unique suffix; whoever
# loses sees the lock vanish and falls into the post-reclaim recreate,
# which fails because the winner already recreated it.
acquire_lock() {
    mkdir -p "${MAINT_STATE_DIR}"
    local lock="${MAINT_STATE_DIR}/.lock"
    local pid_file="${lock}/pid"
    if mkdir "${lock}" 2>/dev/null; then
        MAINT_LOCK="${lock}"
        echo "$$" > "${pid_file}"
        return 0
    fi
    # Couldn't acquire. Check whether the holder is still alive.
    local stale_pid=""
    [[ -f "${pid_file}" ]] && stale_pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -n "${stale_pid}" ]] && kill -0 "${stale_pid}" 2>/dev/null; then
        log "another maintenance run holds ${lock} (pid ${stale_pid}) — exiting"
        exit 4
    fi
    # Holder isn't alive. Race-safe reclaim: atomically rename the stale
    # lock to a PID-unique stash; whichever process's `mv` succeeds owns
    # the right to recreate. `mv` on a directory is atomic on the same
    # filesystem (POSIX rename(2) semantics).
    local stash="${lock}.stale.$$"
    if mv "${lock}" "${stash}" 2>/dev/null; then
        rm -rf "${stash}"
        log "reclaimed stale lock at ${lock} (pid ${stale_pid:-unknown} not alive)"
    else
        log "lost reclaim race for ${lock} — exiting"
        exit 4
    fi
    mkdir "${lock}" || { log "FATAL: could not acquire lock"; exit 4; }
    MAINT_LOCK="${lock}"
    echo "$$" > "${pid_file}"
}

main() {
    trap teardown EXIT
    check_prereqs
    mkdir -p "${MAINT_RUNS_DIR}" "${MAINT_PUBLIC_DIR}"

    # Two simultaneous runs would race on `git fetch && reset --hard
    # && clean` inside MAINT_REPO_DIR; cleaned up in `teardown`.
    acquire_lock

    rotate_scheduler_logs
    maint_start_run
    log "run ${MAINT_RUN_ID} → ${MAINT_RUN_DIR}"

    ensure_repo

    IFS=',' read -r -a checks <<< "${MAINT_CHECKS}"
    for check in "${checks[@]}"; do
        check="$(echo "${check}" | tr -d '[:space:]')"
        [[ -z "${check}" ]] && continue
        run_one_check "${check}" || true   # never abort the loop on a single failure
    done

    maint_run_autofix || true

    maint_finish_run

    log "rendering dashboard → ${MAINT_PUBLIC_DIR}"
    python3 "${MAINT_DIR}/lib/dashboard.py" \
        --runs-dir "${MAINT_RUNS_DIR}" \
        --public-dir "${MAINT_PUBLIC_DIR}" \
        --retention "${MAINT_RUN_RETENTION}"

    notify
    log "done"
}

# --once mode: run a single check against the existing clone, skip the
# autofix/dashboard pipeline. Used for debugging — local edits in the
# state-dir clone are preserved between invocations so you can iterate.
# Acquires the same lock as `main()` so it can't race with a scheduled
# run (the scheduled run would `reset --hard` the clone mid-iteration).
if [[ "${1:-}" == "--once" ]]; then
    MAINT_ONCE=1
    export MAINT_ONCE
    trap teardown EXIT
    check_prereqs
    mkdir -p "${MAINT_RUNS_DIR}" "${MAINT_PUBLIC_DIR}"
    acquire_lock
    # Tag the run ID with `-once` so the dashboard can distinguish a
    # debug run (single check, no autofix) from a real scheduled run.
    maint_start_run once
    ensure_repo 1   # preserve=1 — don't reset the clone
    run_one_check "${2:?usage: automated_maintenance.sh --once <check>}"
    maint_finish_run
    exit 0
fi

main "$@"
