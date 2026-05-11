#!/usr/bin/env bash
# Doc-drift sweep: invoke headless `claude` to find stale references in
# docs/config relative to the current code. Records findings as a markdown
# report. Status is "pass" if zero findings, "fail" if any.
#
# Sourced by nightly.sh.

set -euo pipefail

# Returns rc 0 on no findings, rc 1 on findings, rc 2 on tooling error.
_run_doc_drift() {
    cd "${NIGHTLY_REPO_DIR}"
    if ! command -v claude >/dev/null 2>&1; then
        echo "claude CLI not on PATH — skipping doc_drift" >&2
        return 64
    fi

    local prompt
    prompt="$(cat <<'EOF'
You are doing a documentation drift sweep on the luthien-proxy repository.

Your job: find references in markdown docs, .env.example, docker-compose
files, and config files that contradict the CURRENT state of the code.
You are not making changes — only reporting.

Procedure:
1. Read CLAUDE.md / AGENTS.md and ARCHITECTURE.md to learn the layout.
2. For each top-level doc (README.md, dev-README.md, ARCHITECTURE.md, dev/
   docs, changelog.d/), grep for identifiers, env var names, file paths,
   CLI flags, function names, and config keys.
3. For each, verify the referent still exists with the same name in the
   current code. Use grep against src/ and scripts/.
4. Report contradictions only. Do NOT report stylistic issues, missing
   docs, or things you'd write differently.

Output format — emit ONLY this, nothing else:

# Doc Drift Report

## Findings (N)

### <doc path>:<line or section>
- Stale: `<the stale reference>`
- Reality: `<what the code actually shows>`
- Suggested fix: <one sentence>

(repeat per finding)

If there are zero findings, output exactly:

# Doc Drift Report

No drift detected.
EOF
)"

    # Headless --print: one-shot, no session saved. Read-only tool surface;
    # Claude can grep but can't edit, since we forbid Edit/Write.
    # Prompt is piped via stdin; passing it as a positional arg gets eaten by
    # the variadic --allowedTools flag.
    printf '%s' "${prompt}" | nightly_timeout "${NIGHTLY_TIMEOUT_DOC_DRIFT}" \
        claude --print \
            --no-session-persistence \
            --permission-mode bypassPermissions \
            --disallowedTools "Edit Write NotebookEdit" \
            --allowedTools "Read Glob Grep Bash" \
        > "${NIGHTLY_RUN_DIR}/doc_drift.md"

    # Truth signal: absence of finding-headers. The prompt asks for
    # "### <doc path>:..." entries per finding; if none exist the
    # report is clean. This is less brittle than matching the exact
    # "No drift detected" sentence (whitespace, punctuation, LLM
    # variance).
    if ! grep -q "^### " "${NIGHTLY_RUN_DIR}/doc_drift.md"; then
        return 0
    fi
    return 1
}

nightly_run_doc_drift() {
    local log_path="${NIGHTLY_RUN_DIR}/doc_drift.log"
    local started ended duration rc=0
    echo "[nightly] >>> doc_drift" >&2
    started="$(date +%s)"
    ( _run_doc_drift ) >"${log_path}" 2>&1 || rc=$?
    ended="$(date +%s)"
    duration=$((ended - started))
    local status
    case "${rc}" in
        0) status="pass" ;;
        1) status="fail" ;;
        64) status="skip" ;;
        124) status="error" ;;
        *) status="error" ;;
    esac
    nightly_record_check "doc_drift" "${status}" "doc_drift.log" \
        "{\"duration_s\":${duration},\"exit_code\":${rc},\"report\":\"doc_drift.md\"}"
    echo "[nightly] <<< doc_drift ${status} (${duration}s, rc=${rc})" >&2
}
