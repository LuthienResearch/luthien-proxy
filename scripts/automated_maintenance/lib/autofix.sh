#!/usr/bin/env bash
# Autonomous fix attempt. Runs in MAINT_REPO_DIR on a fresh branch.
# Spawns headless `claude` with a brief covering all failures. If the
# session produces a non-empty diff, push branch + open draft PR.
#
# This is opt-in (AUTOFIX_ENABLED=true). It runs with broad permissions so
# Claude can edit files and run tests; the assumption is that the fix
# branch is reviewed by a human before merge.
#
# Sourced by automated_maintenance.sh.

set -euo pipefail

# Build a markdown brief describing every failed check. Reads results.json
# and concatenates the relevant log tails.
_autofix_build_brief() {
    local brief="$1"
    python3 - "$MAINT_RUN_DIR" "$brief" <<'PY'
import json, pathlib, sys
run_dir = pathlib.Path(sys.argv[1])
brief_path = pathlib.Path(sys.argv[2])
results = json.loads((run_dir / "results.json").read_text())
lines = ["# Maintenance failures", ""]
lines.append(f"Run: {results.get('run_id')}")
lines.append("")
for name, c in results.get("checks", {}).items():
    if c.get("status") not in {"fail", "error"}:
        continue
    lines.append(f"## {name} ({c['status']}, exit {c.get('exit_code')})")
    lines.append("")
    log = run_dir / c.get("log", "")
    if log.exists():
        # errors="replace" mirrors dashboard.py — e2e logs sometimes have
        # raw terminal escapes or non-UTF-8 bytes; one bad byte should
        # not crash brief generation and silently skip the autofix.
        tail = log.read_text(errors="replace").splitlines()[-200:]
        lines.append("```")
        lines.extend(tail)
        lines.append("```")
        lines.append("")
# Doc drift findings, if any.
drift = run_dir / "doc_drift.md"
if drift.exists():
    drift_text = drift.read_text(errors="replace")
    if "No drift detected" not in drift_text:
        lines.append("## doc_drift findings")
        lines.append("")
        lines.append(drift_text)
brief_path.write_text("\n".join(lines))
PY
}

_run_autofix() {
    cd "${MAINT_REPO_DIR}"
    if ! command -v claude >/dev/null 2>&1; then
        echo "claude CLI not on PATH — skipping autofix" >&2
        return 64
    fi
    if ! command -v gh >/dev/null 2>&1; then
        echo "gh CLI not on PATH — skipping autofix" >&2
        return 64
    fi

    local brief="${MAINT_RUN_DIR}/autofix_brief.md"
    _autofix_build_brief "${brief}"

    # Branch name is unique per run (second-resolution timestamp), so a
    # fresh branch is guaranteed — plain `git push -u` below, no need
    # for `--force-with-lease`.
    local branch="${AUTOFIX_BRANCH_PREFIX}/${MAINT_RUN_ID}"
    git checkout -B "${branch}" "origin/${MAINT_REPO_BRANCH}"

    local prompt
    prompt="$(cat <<EOF
You are an autonomous fix bot for luthien-proxy. The automated maintenance
job ran and failures are described in autofix_brief.md (in the run
directory: ${MAINT_RUN_DIR}).

Your task:
1. Read autofix_brief.md and identify root causes.
2. Make minimal, targeted fixes. Do not refactor.
3. After each edit, run the relevant check locally to confirm the fix.
   Match the maintenance run's invocation exactly (the orchestrator runs these
   with --fresh):
     - dev_checks → ./scripts/dev_checks.sh
     - e2e_sqlite → ./scripts/run_e2e.sh sqlite --fresh --no-log
     - e2e_mock  → ./scripts/run_e2e.sh mock --fresh --no-log
     - doc_drift findings → edit the stale reference, no test needed
4. Commit each logical fix separately with a clear message.
5. Stop when you've addressed everything you can. Don't speculate or
   guess if you can't figure something out — leave it for a human.

Constraints (enforced post-session, not just policy):
- Do not edit migrations/, *.env*, or scripts/automated_maintenance/. The orchestrator
  refuses to push a diff that touches any of these paths.
- Do not push or open PRs yourself; the orchestrator does that.
- Do not run e2e_real (ANTHROPIC_API_KEY is unset in this subshell so
  it physically can't authenticate).

When done, write a summary to ${MAINT_RUN_DIR}/autofix_summary.md
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
        printf '%s' "${prompt}" | maint_timeout "${AUTOFIX_TIMEOUT}" \
            claude --print \
                --no-session-persistence \
                --permission-mode bypassPermissions \
                --max-budget-usd "${AUTOFIX_MAX_BUDGET_USD}" \
                --allowedTools "Read Edit Write Glob Grep Bash" \
            > "${MAINT_RUN_DIR}/autofix_session.log" 2>&1
    ) || true

    # Did anything change? Stage everything first so untracked-but-new
    # files don't silently vanish on push. `git diff` alone misses
    # untracked files; `git status --porcelain` catches them.
    if [[ -z "$(git status --porcelain)" ]] && \
       git diff --quiet "origin/${MAINT_REPO_BRANCH}" -- .; then
        echo "autofix produced no diff"
        return 1
    fi
    # Commit anything the session forgot to commit (e.g. created files
    # but never `git add`'d them).
    if [[ -n "$(git status --porcelain)" ]]; then
        git add -A
        git -c user.email=maint-autofix@users.noreply.github.com \
            -c user.name="maint-autofix" \
            commit -m "autofix: capture uncommitted changes from session"
    fi

    # Forbidden-paths gate: refuse to push diffs that touch sensitive
    # areas. The prompt asks Claude to avoid these; this is the
    # enforcement. Logic + unit-test matrix in path_gate.py.
    local changed_paths
    mapfile -t changed_paths < <(git diff --name-only "origin/${MAINT_REPO_BRANCH}"...HEAD)
    if [[ ${#changed_paths[@]} -gt 0 ]]; then
        local gate_out
        gate_out="$(python3 "${MAINT_DIR}/lib/path_gate.py" "${changed_paths[@]}")"
        local gate_rc=$?
        if [[ ${gate_rc} -eq 2 ]]; then
            echo "autofix touched forbidden paths — refusing to push:"
            while IFS= read -r line; do echo "  ${line}"; done <<< "${gate_out}"
            return 2
        fi
    fi

    # Push and open a draft PR.
    git push -u origin "${branch}"
    # Compute the failed-checks summary via argv (not interpolation) so
    # state-dir paths with shell metacharacters don't break the python
    # invocation.
    local failed_checks
    failed_checks="$(python3 - "${MAINT_RUN_DIR}/results.json" <<'PY'
import json, pathlib, sys
r = json.loads(pathlib.Path(sys.argv[1]).read_text())
print(", ".join(n for n, c in r["checks"].items() if c["status"] in {"fail", "error"}))
PY
)"
    local pr_body
    pr_body="$(cat <<EOF
Automated fix attempt from maintenance run ${MAINT_RUN_ID}.

**Failed checks:** ${failed_checks}

See \`autofix_summary.md\` and \`autofix_session.log\` in
\`${MAINT_STATE_DIR}/runs/${MAINT_RUN_ID}/\` for details.

**Review carefully** — these changes are not human-authored.

---
*Posted by automated autofix*
EOF
)"
    local pr_url
    pr_url="$(gh pr create --draft --base "${MAINT_REPO_BRANCH}" \
        --title "maint-fix: ${MAINT_RUN_ID}" \
        --body "${pr_body}")"
    echo "${pr_url}" > "${MAINT_RUN_DIR}/autofix_pr_url.txt"
    echo "opened: ${pr_url}"
    return 0
}

maint_run_autofix() {
    if [[ "${AUTOFIX_ENABLED}" != "true" ]]; then
        echo "[maint] autofix disabled, skipping" >&2
        return 0
    fi
    # Only run if at least one check failed. Pass path via argv (not
    # shell interpolation) so state-dir paths with metacharacters
    # don't break the python literal.
    local any_fail
    any_fail="$(python3 - "${MAINT_RUN_DIR}/results.json" <<'PY'
import json, pathlib, sys
r = json.loads(pathlib.Path(sys.argv[1]).read_text())
print(any(c.get("status") in {"fail", "error"} for c in r.get("checks", {}).values()))
PY
)"
    if [[ "${any_fail}" != "True" ]]; then
        echo "[maint] all checks passed, no autofix needed" >&2
        return 0
    fi

    local log_path="${MAINT_RUN_DIR}/autofix.log"
    local started ended duration rc=0
    echo "[maint] >>> autofix" >&2
    started="$(date +%s)"
    ( _run_autofix ) >"${log_path}" 2>&1 || rc=$?
    ended="$(date +%s)"
    duration=$((ended - started))
    local status
    case "${rc}" in
        0) status="opened_pr" ;;
        1) status="no_diff" ;;
        2) status="forbidden_paths" ;;
        64) status="skip" ;;
        124) status="timeout" ;;
        *) status="error" ;;
    esac
    local pr_url=""
    [[ -f "${MAINT_RUN_DIR}/autofix_pr_url.txt" ]] && \
        pr_url="$(cat "${MAINT_RUN_DIR}/autofix_pr_url.txt")"
    python3 - "$MAINT_RUN_DIR" "$status" "$duration" "$rc" "$pr_url" <<'PY'
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
    echo "[maint] <<< autofix ${status} (${duration}s, rc=${rc})" >&2
}
