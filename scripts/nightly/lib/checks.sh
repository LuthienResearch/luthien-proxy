#!/usr/bin/env bash
# Per-check runners. Each one cd's into NIGHTLY_REPO_DIR, runs the work
# with a timeout, captures logs, and records pass/fail in results.json.
#
# Sourced by nightly.sh.

set -euo pipefail

# Run a single check. The actual runner is a function `_run_<name>` defined
# below. Output goes to NIGHTLY_RUN_DIR/<name>.log. Status is recorded via
# nightly_record_check.
nightly_run_check() {
    local name="$1"
    local log_path="${NIGHTLY_RUN_DIR}/${name}.log"
    local fn="_run_${name}"
    if ! declare -F "${fn}" >/dev/null; then
        echo "[nightly] unknown check: ${name}" >&2
        nightly_record_check "${name}" "error" "${name}.log" "{\"error\":\"unknown check\"}"
        return 1
    fi
    echo "[nightly] >>> ${name}" >&2
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
    nightly_record_check "${name}" "${status}" "${name}.log" \
        "{\"duration_s\":${duration},\"exit_code\":${rc}}"
    echo "[nightly] <<< ${name} ${status} (${duration}s, rc=${rc})" >&2
}

# --- runners ---

_run_dev_checks() {
    cd "${NIGHTLY_REPO_DIR}"
    nightly_timeout "${NIGHTLY_TIMEOUT_DEV_CHECKS}" ./scripts/dev_checks.sh
}

_run_e2e_sqlite() {
    cd "${NIGHTLY_REPO_DIR}"
    nightly_timeout "${NIGHTLY_TIMEOUT_E2E}" ./scripts/run_e2e.sh sqlite --fresh --no-log
}

_run_e2e_mock() {
    cd "${NIGHTLY_REPO_DIR}"
    nightly_timeout "${NIGHTLY_TIMEOUT_E2E}" ./scripts/run_e2e.sh mock --fresh --no-log
}

_run_e2e_real() {
    cd "${NIGHTLY_REPO_DIR}"
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        echo "ANTHROPIC_API_KEY unset — skipping e2e_real"
        return 64  # caller maps non-zero, but we want "skip" — handled below
    fi
    nightly_timeout "${NIGHTLY_TIMEOUT_E2E}" ./scripts/run_e2e.sh real --fresh --no-log
}

# e2e_real special-cases: rc 64 means "skipped due to no API key".
# Wrap the standard runner with a post-hook.
nightly_run_e2e_real() {
    local log_path="${NIGHTLY_RUN_DIR}/e2e_real.log"
    local started ended duration rc=0
    echo "[nightly] >>> e2e_real" >&2
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
    nightly_record_check "e2e_real" "${status}" "e2e_real.log" \
        "{\"duration_s\":${duration},\"exit_code\":${rc}}"
    echo "[nightly] <<< e2e_real ${status} (${duration}s, rc=${rc})" >&2
}
