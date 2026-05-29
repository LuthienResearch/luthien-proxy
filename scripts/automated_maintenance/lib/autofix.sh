#!/usr/bin/env bash
# Autonomous fix attempts, one per failing concern.
#
# Each failing check is a "concern" and gets its own focused fix attempt on
# its own branch (maint-fix/<concern>/<run_id>) and, if it produces a diff,
# its own single-concern draft PR. This keeps PRs reviewable and lets a
# novel failure get its own PR even while another concern's fix is pending.
#
# Per-concern dedup: before attempting a concern, we check for an already-open
# PR for that concern (branch maint-fix/<concern>/*). If one exists, we skip
# the concern — don't stack a duplicate while a fix is in review. A different,
# novel concern still gets its own PR.
#
# This is opt-in (AUTOFIX_ENABLED=true). It runs with broad permissions so
# Claude can edit files and run tests; fix branches are reviewed by a human
# before merge (PRs open as drafts).
#
# Sourced by automated_maintenance.sh.

set -euo pipefail

# Build a focused brief for ONE concern. doc_drift pulls in its findings
# report; every other check pulls in its log tail.
_autofix_build_brief_for() {
    local concern="$1" brief="$2"
    python3 - "$MAINT_RUN_DIR" "$concern" "$brief" <<'PY'
import json, pathlib, sys
run_dir = pathlib.Path(sys.argv[1])
concern = sys.argv[2]
brief_path = pathlib.Path(sys.argv[3])
results = json.loads((run_dir / "results.json").read_text())
c = results.get("checks", {}).get(concern, {})
lines = [
    f"# Maintenance failure: {concern}",
    "",
    f"Run: {results.get('run_id')}",
    "",
    f"## {concern} ({c.get('status')}, exit {c.get('exit_code')})",
    "",
]
if concern == "doc_drift":
    drift = run_dir / (c.get("report") or "doc_drift.md")
    if drift.exists():
        lines.append(drift.read_text(errors="replace"))
else:
    log = run_dir / c.get("log", "")
    if log.exists():
        # errors="replace": e2e logs sometimes carry raw terminal escapes or
        # non-UTF-8 bytes; one bad byte must not crash brief generation.
        tail = log.read_text(errors="replace").splitlines()[-200:]
        lines.append("```")
        lines.extend(tail)
        lines.append("```")
brief_path.write_text("\n".join(lines))
PY
}

# Echo the URL of an already-open autofix PR for this concern, if any.
# rc 0 + URL on stdout when one exists; rc 1 when none; rc 2 when the GitHub
# query itself failed (caller fails closed rather than risk a duplicate).
#
# Dedup correctness depends on the trailing `/`: `head:` matches by prefix, so
# `head:maint-fix/<concern>/` matches `maint-fix/<concern>/<run_id>` but not a
# concern whose name extends this one. This holds only while no concern name
# is a prefix of another. Today's concerns (the check names: dev_checks,
# e2e_sqlite, e2e_mock, e2e_real, doc_drift) are all prefix-distinct; keep it
# that way, or this dedup could match across concerns.
_autofix_open_pr_for() {
    local concern="$1" url
    url="$(gh pr list \
        --repo "$(git -C "${MAINT_REPO_DIR}" remote get-url origin)" \
        --state open \
        --search "head:${AUTOFIX_BRANCH_PREFIX}/${concern}/" \
        --json url --jq '.[0].url // empty' 2>/dev/null)" || return 2
    [[ -n "${url}" ]] || return 1
    echo "${url}"
    return 0
}

# Attempt a fix for a single concern. Returns:
#   0   opened a draft PR
#   65  session produced no diff
#   2   touched forbidden paths (refused to push)
#   *   other error
# Writes the PR url (on success) to autofix_pr_url.<concern>.txt.
_run_autofix_concern() {
    local concern="$1"
    cd "${MAINT_REPO_DIR}"

    local brief="${MAINT_RUN_DIR}/autofix_brief.${concern}.md"
    _autofix_build_brief_for "${concern}" "${brief}"

    local branch="${AUTOFIX_BRANCH_PREFIX}/${concern}/${MAINT_RUN_ID}"
    git checkout -B "${branch}" "origin/${MAINT_REPO_BRANCH}"
    # `checkout -B` only resets tracked files. Concerns run serially in the
    # same clone, so without this any untracked leftovers from the previous
    # concern's session (scratch files Claude wrote but never `git add`ed)
    # would survive and get swept into THIS concern's `git add -A` below —
    # contaminating its diff and possibly its forbidden-paths verdict.
    git clean -fdx

    local prompt
    prompt="$(cat <<EOF
You are an autonomous fix bot for luthien-proxy. The automated maintenance
job found a problem with the "${concern}" check. It is described in
${brief} (in the run directory: ${MAINT_RUN_DIR}).

Fix ONLY the ${concern} concern in this session — nothing else. A separate
session handles each other concern, so do not range beyond this one.

Your task:
1. Read the brief and identify the root cause(s) of the ${concern} failure.
2. Make minimal, targeted fixes. Do not refactor.
3. Verify locally where it makes sense. Match the maintenance run's
   invocation (the orchestrator runs these with --fresh):
     - dev_checks → ./scripts/dev_checks.sh
     - e2e_sqlite → ./scripts/run_e2e.sh sqlite --fresh --no-log
     - e2e_mock  → ./scripts/run_e2e.sh mock --fresh --no-log
     - doc_drift → edit the stale reference to match the code; no test needed
4. Commit each logical fix separately with a clear message.
5. Stop when you've addressed what you can. Don't speculate or guess; leave
   anything uncertain for a human.

Constraints (enforced post-session, not just policy):
- Do not edit migrations/, *.env*, or scripts/automated_maintenance/. The
  orchestrator refuses to push a diff that touches any of these paths.
- Do not push or open PRs yourself; the orchestrator does that.
- Do not run e2e_real (ANTHROPIC_API_KEY is unset so it can't authenticate).

When done, write a summary to ${MAINT_RUN_DIR}/autofix_summary.${concern}.md
listing what you fixed, what you couldn't, and why.
EOF
)"

    local sess_rc=0
    (
        unset ANTHROPIC_API_KEY
        printf '%s' "${prompt}" | maint_timeout "${AUTOFIX_TIMEOUT}" \
            claude --print \
                --no-session-persistence \
                --permission-mode bypassPermissions \
                --max-budget-usd "${AUTOFIX_MAX_BUDGET_USD}" \
                --allowedTools "Read Edit Write Glob Grep Bash" \
            > "${MAINT_RUN_DIR}/autofix_session.${concern}.log" 2>&1
    ) || sess_rc=$?
    # A timed-out session (gtimeout → 124) was killed mid-edit; don't push its
    # partial, unreviewed work as if it were a finished fix. Other nonzero
    # exits are tolerated — `claude` may exit nonzero yet still have produced a
    # complete, valid diff, so we fall through to the diff check below.
    if [[ ${sess_rc} -eq 124 ]]; then
        echo "autofix(${concern}) timed out after ${AUTOFIX_TIMEOUT}s — not pushing partial work"
        return 124
    fi

    # No change? Nothing to push. `git status --porcelain` catches untracked
    # files that `git diff` alone misses.
    if [[ -z "$(git status --porcelain)" ]] && \
       git diff --quiet "origin/${MAINT_REPO_BRANCH}" -- .; then
        echo "autofix(${concern}) produced no diff"
        return 65
    fi
    if [[ -n "$(git status --porcelain)" ]]; then
        git add -A
        git commit -m "autofix(${concern}): capture uncommitted changes from session"
    fi

    # Forbidden-paths gate (logic + tests in path_gate.py).
    local changed_paths=()
    while IFS= read -r line; do
        [[ -n "${line}" ]] && changed_paths+=("${line}")
    done < <(git diff --name-only "origin/${MAINT_REPO_BRANCH}"...HEAD)
    if [[ ${#changed_paths[@]} -gt 0 ]]; then
        local gate_out gate_rc=0
        gate_out="$(python3 "${MAINT_DIR}/lib/path_gate.py" "${changed_paths[@]}")" || gate_rc=$?
        case "${gate_rc}" in
            0) ;;
            2)
                echo "autofix(${concern}) touched forbidden paths — refusing to push:"
                while IFS= read -r line; do echo "  ${line}"; done <<< "${gate_out}"
                return 2
                ;;
            *)
                echo "path_gate.py exited unexpectedly (rc=${gate_rc}) — refusing to push:"
                echo "${gate_out}"
                return 2
                ;;
        esac
    fi

    git push -u origin "${branch}"
    local pr_body
    pr_body="$(cat <<EOF
Automated fix attempt for the **${concern}** check, from maintenance run ${MAINT_RUN_ID}.

This PR addresses a single concern (${concern}). Other failing checks, if any,
get their own PRs.

See \`autofix_summary.${concern}.md\` and \`autofix_session.${concern}.log\` in
\`${MAINT_STATE_DIR}/runs/${MAINT_RUN_ID}/\` for details.

**Review carefully** — these changes are not human-authored.

---
*Posted by automated autofix*
EOF
)"
    local pr_url pr_rc=0
    pr_url="$(gh pr create --draft --base "${MAINT_REPO_BRANCH}" \
        --title "maint-fix(${concern}): ${MAINT_RUN_ID}" \
        --body "${pr_body}")" || pr_rc=$?
    if [[ ${pr_rc} -ne 0 ]]; then
        echo "gh pr create failed (rc=${pr_rc}) — deleting orphan remote branch"
        git push origin --delete "${branch}" >/dev/null 2>&1 || true
        return "${pr_rc}"
    fi
    echo "${pr_url}" > "${MAINT_RUN_DIR}/autofix_pr_url.${concern}.txt"
    echo "autofix(${concern}) opened: ${pr_url}"
    return 0
}

# Record one concern's autofix outcome into results.json under
# .autofix.<concern>. Keeps each concern's status/pr independent.
_autofix_record() {
    local concern="$1" status="$2" duration="$3" rc="$4" pr_url="${5:-}" existing_pr="${6:-}"
    python3 - "$MAINT_RUN_DIR" "$concern" "$status" "$duration" "$rc" "$pr_url" "$existing_pr" <<'PY'
import json, pathlib, sys
run_dir, concern, status, duration, rc, pr_url, existing_pr = sys.argv[1:8]
p = pathlib.Path(run_dir, "results.json")
data = json.loads(p.read_text())
af = data.get("autofix")
if not isinstance(af, dict):
    af = {}
entry = {"status": status, "duration_s": int(duration), "exit_code": int(rc)}
if pr_url:
    entry["pr_url"] = pr_url
if existing_pr:
    entry["existing_pr"] = existing_pr
af[concern] = entry
data["autofix"] = af
p.write_text(json.dumps(data, indent=2) + "\n")
PY
}

maint_run_autofix() {
    if [[ "${AUTOFIX_ENABLED}" != "true" ]]; then
        echo "[maint] autofix disabled, skipping" >&2
        return 0
    fi
    if ! command -v claude >/dev/null 2>&1; then
        echo "[maint] claude CLI not on PATH — skipping autofix" >&2
        return 0
    fi
    if ! command -v gh >/dev/null 2>&1; then
        echo "[maint] gh CLI not on PATH — skipping autofix" >&2
        return 0
    fi

    # Concerns = failing checks, in declared order.
    local concerns=()
    while IFS= read -r line; do
        [[ -n "${line}" ]] && concerns+=("${line}")
    done < <(python3 - "${MAINT_RUN_DIR}/results.json" <<'PY'
import json, pathlib, sys
r = json.loads(pathlib.Path(sys.argv[1]).read_text())
for name, c in r.get("checks", {}).items():
    if c.get("status") in {"fail", "error"}:
        print(name)
PY
)
    if [[ ${#concerns[@]} -eq 0 ]]; then
        echo "[maint] all checks passed, no autofix needed" >&2
        return 0
    fi

    # Repo-local commit identity for the autofix branches — set once for the
    # clone rather than per concern.
    git -C "${MAINT_REPO_DIR}" config user.email nightly-autofix@users.noreply.github.com
    git -C "${MAINT_REPO_DIR}" config user.name "nightly-autofix"

    local concern
    for concern in "${concerns[@]}"; do
        echo "[maint] >>> autofix(${concern})" >&2

        # Per-concern dedup: skip if an open PR already covers this concern.
        local existing="" pr_rc=0
        existing="$(_autofix_open_pr_for "${concern}")" || pr_rc=$?
        if [[ ${pr_rc} -eq 0 ]]; then
            echo "[maint] <<< autofix(${concern}) skipped — open PR: ${existing}" >&2
            _autofix_record "${concern}" "skipped_existing_pr" 0 0 "" "${existing}"
            continue
        elif [[ ${pr_rc} -eq 2 ]]; then
            # Couldn't query GitHub — fail closed (don't risk a duplicate).
            echo "[maint] <<< autofix(${concern}) skipped — could not query open PRs" >&2
            _autofix_record "${concern}" "skipped_query_failed" 0 0 "" ""
            continue
        fi

        local started ended duration rc=0
        started="$(date +%s)"
        ( _run_autofix_concern "${concern}" ) \
            > "${MAINT_RUN_DIR}/autofix.${concern}.log" 2>&1 || rc=$?
        ended="$(date +%s)"
        duration=$((ended - started))
        local status
        # rc=2 here can only mean forbidden_paths: the other rc=2 source
        # (_autofix_open_pr_for query failure) takes the early `continue`
        # above and never reaches _run_autofix_concern. Keep that early-out
        # if refactoring, or this mapping becomes ambiguous.
        case "${rc}" in
            0) status="opened_pr" ;;
            65) status="no_diff" ;;
            2) status="forbidden_paths" ;;
            124) status="timeout" ;;
            *) status="error" ;;
        esac
        local pr_url=""
        [[ -f "${MAINT_RUN_DIR}/autofix_pr_url.${concern}.txt" ]] && \
            pr_url="$(cat "${MAINT_RUN_DIR}/autofix_pr_url.${concern}.txt")"
        _autofix_record "${concern}" "${status}" "${duration}" "${rc}" "${pr_url}" ""
        echo "[maint] <<< autofix(${concern}) ${status} (${duration}s, rc=${rc})" >&2
    done
}
