#!/usr/bin/env bash
# Per-check runners. Each one cd's into MAINT_REPO_DIR, runs the work
# with a timeout, captures logs, and records pass/fail in results.json.
#
# Sourced by automated_maintenance.sh.

set -euo pipefail

# Run a single check. The actual runner is a function `_run_<name>` defined
# below. Output goes to MAINT_RUN_DIR/<name>.log. Status is recorded via
# maint_record_check.
maint_run_check() {
    local name="$1"
    local log_path="${MAINT_RUN_DIR}/${name}.log"
    local fn="_run_${name}"
    if ! declare -F "${fn}" >/dev/null; then
        echo "[maint] unknown check: ${name}" >&2
        maint_record_check "${name}" "error" "${name}.log" "{\"error\":\"unknown check\"}"
        return 1
    fi
    echo "[maint] >>> ${name}" >&2
    local started ended duration rc=0
    started="$(date +%s)"
    ( "${fn}" ) >"${log_path}" 2>&1 || rc=$?
    ended="$(date +%s)"
    duration=$((ended - started))
    local status
    if [[ ${rc} -eq 0 ]]; then
        status="pass"
    elif [[ ${rc} -eq 124 ]]; then
        status="error"  # timeout
    else
        status="fail"
    fi
    maint_record_check "${name}" "${status}" "${name}.log" \
        "{\"duration_s\":${duration},\"exit_code\":${rc}}"
    echo "[maint] <<< ${name} ${status} (${duration}s, rc=${rc})" >&2
}

# --- runners ---

_run_dev_checks() {
    cd "${MAINT_REPO_DIR}"
    maint_timeout "${MAINT_TIMEOUT_DEV_CHECKS}" ./scripts/dev_checks.sh
}

_run_e2e_sqlite() {
    cd "${MAINT_REPO_DIR}"
    maint_timeout "${MAINT_TIMEOUT_E2E}" ./scripts/run_e2e.sh sqlite --fresh --no-log
}

_run_e2e_mock() {
    cd "${MAINT_REPO_DIR}"
    maint_timeout "${MAINT_TIMEOUT_E2E}" ./scripts/run_e2e.sh mock --fresh --no-log
}

# NOTE: do NOT route this via `maint_run_check` — the rc=64 "skip"
# convention is only translated by the dedicated `maint_run_e2e_real`
# wrapper below. The generic dispatcher would record rc=64 as a fail.
_run_e2e_real() {
    cd "${MAINT_REPO_DIR}"
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        echo "ANTHROPIC_API_KEY unset — skipping e2e_real"
        return 64
    fi
    maint_timeout "${MAINT_TIMEOUT_E2E}" ./scripts/run_e2e.sh real --fresh --no-log
}

# e2e_real special-cases: rc 64 means "skipped due to no API key".
# Wrap the standard runner with a post-hook.
maint_run_e2e_real() {
    local log_path="${MAINT_RUN_DIR}/e2e_real.log"
    local started ended duration rc=0
    echo "[maint] >>> e2e_real" >&2
    started="$(date +%s)"
    ( _run_e2e_real ) >"${log_path}" 2>&1 || rc=$?
    ended="$(date +%s)"
    duration=$((ended - started))
    local status
    case "${rc}" in
        0) status="pass" ;;
        64) status="skip" ;;
        124) status="error" ;;
        *) status="fail" ;;
    esac
    maint_record_check "e2e_real" "${status}" "e2e_real.log" \
        "{\"duration_s\":${duration},\"exit_code\":${rc}}"
    echo "[maint] <<< e2e_real ${status} (${duration}s, rc=${rc})" >&2
}
