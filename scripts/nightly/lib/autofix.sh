#!/usr/bin/env bash
# Autonomous fix attempt. Runs in NIGHTLY_REPO_DIR on a fresh branch.
# Spawns headless `claude` with a brief covering all failures. If the
# session produces a non-empty diff, push branch + open draft PR.
#
# This is opt-in (AUTOFIX_ENABLED=true). It runs with broad permissions so
# Claude can edit files and run tests; the assumption is that the fix
# branch is reviewed by a human before merge.
#
# Sourced by nightly.sh.

set -euo pipefail

# Build a markdown brief describing every failed check. Reads results.json
# and concatenates the relevant log tails.
_autofix_build_brief() {
    local brief="$1"
    python3 - "$NIGHTLY_RUN_DIR" "$brief" <<'PY'
import json, pathlib, sys
run_dir = pathlib.Path(sys.argv[1])
brief_path = pathlib.Path(sys.argv[2])
results = json.loads((run_dir / "results.json").read_text())
lines = ["# Nightly failures", ""]
lines.append(f"Run: {results.get('run_id')}")
lines.append("")
for name, c in results.get("checks", {}).items():
    if c.get("status") not in {"fail", "error"}:
        continue
    lines.append(f"## {name} ({c['status']}, exit {c.get('exit_code')})")
    lines.append("")
    log = run_dir / c.get("log", "")
    if log.exists():
        tail = log.read_text().splitlines()[-200:]
        lines.append("```")
        lines.extend(tail)
        lines.append("```")
        lines.append("")
# Doc drift findings, if any.
drift = run_dir / "doc_drift.md"
if drift.exists() and "No drift detected" not in drift.read_text():
    lines.append("## doc_drift findings")
    lines.append("")
    lines.append(drift.read_text())
brief_path.write_text("\n".join(lines))
PY
}

_run_autofix() {
    cd "${NIGHTLY_REPO_DIR}"
    if ! command -v claude >/dev/null 2>&1; then
        echo "claude CLI not on PATH — skipping autofix" >&2
        return 64
    fi
    if ! command -v gh >/dev/null 2>&1; then
        echo "gh CLI not on PATH — skipping autofix" >&2
        return 64
    fi

    local brief="${NIGHTLY_RUN_DIR}/autofix_brief.md"
    _autofix_build_brief "${brief}"

    local branch="${AUTOFIX_BRANCH_PREFIX}/${NIGHTLY_RUN_ID}"
    git checkout -B "${branch}" "origin/${NIGHTLY_REPO_BRANCH}"

    local prompt
    prompt="$(cat <<EOF
You are an autonomous fix bot for luthien-proxy. The nightly maintenance
job ran and failures are described in autofix_brief.md (in the run
directory: ${NIGHTLY_RUN_DIR}).

Your task:
1. Read autofix_brief.md and identify root causes.
2. Make minimal, targeted fixes. Do not refactor.
3. After each edit, run the relevant check locally to confirm the fix:
   - dev_checks → ./scripts/dev_checks.sh
   - e2e_sqlite → ./scripts/run_e2e.sh sqlite --no-log
   - e2e_mock → ./scripts/run_e2e.sh mock --no-log
   - doc_drift findings → fix the stale reference, no test needed
4. Commit each logical fix separately with a clear message.
5. Stop when you've addressed everything you can. Don't speculate or
   guess if you can't figure something out — leave it for a human.

Constraints:
- Do not edit migrations, secrets, or .env files.
- Do not push or open PRs yourself; the orchestrator does that.
- Do not run e2e_real (it costs money).

When done, write a summary to ${NIGHTLY_RUN_DIR}/autofix_summary.md
listing what you fixed, what you couldn't fix, and why.
EOF
)"

    # AUTOFIX_MAX_BUDGET_USD caps API spend per attempt. Prompt via stdin
    # — variadic --allowedTools would otherwise eat it. ANTHROPIC_API_KEY
    # is unset in this subshell so the session can't hit the real
    # Anthropic API (e.g. via ./scripts/run_e2e.sh real). The prompt
    # also asks Claude not to, but the env strip enforces it.
    (
        unset ANTHROPIC_API_KEY
        printf '%s' "${prompt}" | nightly_timeout "${AUTOFIX_TIMEOUT}" \
            claude --print \
                --no-session-persistence \
                --permission-mode bypassPermissions \
                --max-budget-usd "${AUTOFIX_MAX_BUDGET_USD}" \
                --allowedTools "Read Edit Write Glob Grep Bash" \
            > "${NIGHTLY_RUN_DIR}/autofix_session.log" 2>&1
    ) || true

    # Did anything change? Stage everything first so untracked-but-new
    # files don't silently vanish on push. `git diff` alone misses
    # untracked files; `git status --porcelain` catches them.
    if [[ -z "$(git status --porcelain)" ]] && \
       git diff --quiet "origin/${NIGHTLY_REPO_BRANCH}" -- .; then
        echo "autofix produced no diff"
        return 1
    fi
    # Commit anything the session forgot to commit (e.g. created files
    # but never `git add`'d them).
    if [[ -n "$(git status --porcelain)" ]]; then
        git add -A
        git -c user.email=nightly-autofix@luthien -c user.name="nightly-autofix" \
            commit -m "autofix: capture uncommitted changes from session"
    fi

    # Push and open a draft PR.
    git push --force-with-lease -u origin "${branch}"
    # Compute the failed-checks summary via argv (not interpolation) so
    # state-dir paths with shell metacharacters don't break the python
    # invocation.
    local failed_checks
    failed_checks="$(python3 - "${NIGHTLY_RUN_DIR}/results.json" <<'PY'
import json, pathlib, sys
r = json.loads(pathlib.Path(sys.argv[1]).read_text())
print(", ".join(n for n, c in r["checks"].items() if c["status"] in {"fail", "error"}))
PY
)"
    local pr_body
    pr_body="$(cat <<EOF
Automated fix attempt from nightly run ${NIGHTLY_RUN_ID}.

**Failed checks:** ${failed_checks}

See \`autofix_summary.md\` and \`autofix_session.log\` in
\`${NIGHTLY_STATE_DIR}/runs/${NIGHTLY_RUN_ID}/\` for details.

**Review carefully** — these changes are not human-authored.

---
*Posted by nightly autofix*
EOF
)"
    local pr_url
    pr_url="$(gh pr create --draft --base "${NIGHTLY_REPO_BRANCH}" \
        --title "nightly-fix: ${NIGHTLY_RUN_ID}" \
        --body "${pr_body}")"
    echo "${pr_url}" > "${NIGHTLY_RUN_DIR}/autofix_pr_url.txt"
    echo "opened: ${pr_url}"
    return 0
}

nightly_run_autofix() {
    if [[ "${AUTOFIX_ENABLED}" != "true" ]]; then
        echo "[nightly] autofix disabled, skipping" >&2
        return 0
    fi
    # Only run if at least one check failed.
    local any_fail
    any_fail="$(python3 -c "
import json, pathlib
r = json.loads(pathlib.Path('${NIGHTLY_RUN_DIR}/results.json').read_text())
print(any(c.get('status') in {'fail','error'} for c in r.get('checks',{}).values()))
")"
    if [[ "${any_fail}" != "True" ]]; then
        echo "[nightly] all checks passed, no autofix needed" >&2
        return 0
    fi

    local log_path="${NIGHTLY_RUN_DIR}/autofix.log"
    local started ended duration rc=0
    echo "[nightly] >>> autofix" >&2
    started="$(date +%s)"
    ( _run_autofix ) >"${log_path}" 2>&1 || rc=$?
    ended="$(date +%s)"
    duration=$((ended - started))
    local status
    case "${rc}" in
        0) status="opened_pr" ;;
        1) status="no_diff" ;;
        64) status="skip" ;;
        124) status="timeout" ;;
        *) status="error" ;;
    esac
    local pr_url=""
    [[ -f "${NIGHTLY_RUN_DIR}/autofix_pr_url.txt" ]] && \
        pr_url="$(cat "${NIGHTLY_RUN_DIR}/autofix_pr_url.txt")"
    python3 - "$NIGHTLY_RUN_DIR" "$status" "$duration" "$rc" "$pr_url" <<'PY'
import json, pathlib, sys
run_dir, status, duration, rc, pr_url = sys.argv[1:6]
p = pathlib.Path(run_dir, "results.json")
data = json.loads(p.read_text())
data["autofix"] = {
    "status": status,
    "duration_s": int(duration),
    "exit_code": int(rc),
    "pr_url": pr_url or None,
}
p.write_text(json.dumps(data, indent=2) + "\n")
PY
    echo "[nightly] <<< autofix ${status} (${duration}s, rc=${rc})" >&2
}
