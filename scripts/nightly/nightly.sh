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
    if [[ -f "${NIGHTLY_REPO_DIR}/docker-compose.yaml" ]] && command -v docker >/dev/null 2>&1; then
        ( cd "${NIGHTLY_REPO_DIR}" && docker compose down -v --remove-orphans ) >/dev/null 2>&1 || true
    fi
}

notify() {
    local url="${NIGHTLY_WEBHOOK_URL}"
    [[ -z "${url}" ]] && return 0
    local overall summary
    overall="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('overall','?'))" "${NIGHTLY_RUN_DIR}/results.json")"
    summary="$(python3 -c "
import json, sys
r = json.load(open(sys.argv[1]))
checks = r.get('checks', {})
parts = [f\"{n}={c.get('status','?')}\" for n, c in checks.items()]
af = r.get('autofix') or {}
if af: parts.append(f\"autofix={af.get('status','?')}\")
print(' '.join(parts))
" "${NIGHTLY_RUN_DIR}/results.json")"
    curl -sfS -X POST -H "Content-Type: application/json" \
        -d "{\"text\":\"nightly ${NIGHTLY_RUN_ID} ${overall}: ${summary}\"}" \
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
    nightly_start_run
    ensure_repo 1   # preserve=1 — don't reset the clone
    run_one_check "${2:?usage: nightly.sh --once <check>}"
    nightly_finish_run
    exit 0
fi

main "$@"
