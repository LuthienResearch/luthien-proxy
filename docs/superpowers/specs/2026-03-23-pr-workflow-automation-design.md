# PR Workflow Automation

**Date:** 2026-03-23
**Status:** Approved

## Problem

The PR submission workflow has predictable manual steps that waste agent time and require unnecessary human intervention. Analysis of 25 sessions (Mar 8-23) identified these friction points ranked by frequency:

1. **Two-pass dev_checks dance** — `dev_checks.sh` runs `ruff format` + `ruff check --fix` which modify files, then fails because the tree is dirty. Agent must commit format fixes, re-run. Happens 2-3x per PR.
2. **Manual "check PR comments" round-trips** — user must explicitly tell the agent to look at PR feedback. Background polling monitors are unreliable (the self-chaining bash agent pattern can't spawn successor agents).
3. **Changelog fragments forgotten** — CI posts a reminder but agents don't proactively create one.
4. **Worktree script path failures** — `./scripts/` not found because worktrees have a different working directory.

## Design

Four changes, split into mechanical fixes (A1-A3) and monitoring (B1).

### A1: Single-pass `dev_checks.sh`

**Current behavior:** The script requires a clean tree upfront, runs formatters that dirty the tree, then fails on a post-run clean tree check. Agents commit format fixes and re-run.

**New behavior:** Two-phase structure — fix first, then gate.

```
Phase 1: Fix
  - shellcheck
  - ruff format
  - ruff check --fix
  - If tree is dirty: git add -u, report what changed

Phase 2: Gate
  - ruff check (gating)
  - pyright (gating)
  - pytest (gating)
  - radon (report-only)
  - Clean tree check (safety net — should pass since Phase 1 staged everything)
```

**Changes:**
- Remove the pre-check for clean tree (current lines 6-10)
- After format+fix, auto-stage changes instead of failing
- Keep the post-gate clean tree check as a safety net
- shellcheck stays in Phase 1 (it doesn't modify files, but runs early to fail fast on script issues)

**Files:** `scripts/dev_checks.sh`

### A2: Auto-generate changelog fragment at PR creation

**Current behavior:** Agents forget. CI posts a reminder comment that often goes unaddressed.

**New behavior:** After `gh pr create` returns the PR number, generate a changelog fragment with content inferred from commits.

**Mechanics:**
- Filename: `changelog.d/<branch-name>.md` (consistent with existing convention). Falls back to `pr-<NUMBER>.md` if branch name is unavailable.
- Infer category from commit prefixes: `feat`→Features, `fix`→Fixes, `refactor`→Refactors, else→Chores & Docs
- Summarize from commit messages on the branch
- If a fragment already exists for this branch, skip
- Format follows existing `changelog.d/README.md` convention:

```markdown
---
category: Features
pr: <NUMBER>
---

**Title**: Description inferred from commits
```

**Why skill steps, not a hook:** A `PostToolUse` hook on Bash matching `gh pr create` is fragile — it fires on every Bash call and must pattern-match the command string. Skill steps are explicit, readable, and the agent already has full context of what changed.

**Files:** `/pr` skill, `/dev` skill (Stage 6), `/finish-ticket` skill (Phase 2)

### A3: Worktree-safe script paths

**Current behavior:** Scripts referenced as `./scripts/dev_checks.sh` fail in worktrees.

**New behavior:** Scripts resolve the repo root at startup:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
```

Skill instructions and CLAUDE.md references use `"$(git rev-parse --show-toplevel)/scripts/dev_checks.sh"` instead of `./scripts/dev_checks.sh`.

**Files:** `scripts/dev_checks.sh`, `scripts/format_all.sh`, CLAUDE.md, skill files that reference scripts

### B1: Same-session PR monitoring

**Current behavior:** After marking a PR ready, the agent goes idle. The user must manually ask it to check for comments or CI failures. The self-chaining bash agent pattern documented in CLAUDE.md never worked (a bash script can't spawn successor agents).

**New behavior:** After `gh pr ready` (or `gh pr create` for non-draft PRs), launch one background Bash command with `run_in_background: true` that polls every 90 seconds and exits when something needs attention.

**What it monitors:**
- CI failures (`gh pr checks`)
- New comments (`gh pr view --json comments`)
- New reviews (`gh pr view --json reviews`)
- Merge conflicts (`gh pr view --json mergeable`)
- PR merged/closed (stop monitoring)

**On alert:** The background command exits with structured output (e.g., `ALERT:CI_FAILURE`, `ALERT:NEW_COMMENTS`). The agent gets notified via `run_in_background` completion and acts — fetching full details and addressing the issue using `/address-pr` logic.

**Monitor script:**

```bash
PR=<number>
PREV_COMMENTS=$(gh pr view $PR --json comments --jq '.comments | length')
PREV_REVIEWS=$(gh pr view $PR --json reviews --jq '.reviews | length')
ITER=0; MAX_ITER=80

while [ "$ITER" -lt "$MAX_ITER" ]; do
    sleep 90
    ITER=$((ITER + 1))

    # CI status
    CI=$(gh pr checks $PR --json state --jq '.[].state' 2>/dev/null | sort -u)
    if echo "$CI" | grep -q "FAILURE"; then
        echo "ALERT:CI_FAILURE"
        gh pr checks $PR
        exit 0
    fi

    # New comments
    NEW_COMMENTS=$(gh pr view $PR --json comments --jq '.comments | length')
    if [ "$NEW_COMMENTS" -gt "$PREV_COMMENTS" ]; then
        echo "ALERT:NEW_COMMENTS"
        gh pr view $PR --json comments --jq '.comments[-1] | "@\(.author.login): \(.body[0:500])"'
        exit 0
    fi

    # New reviews
    NEW_REVIEWS=$(gh pr view $PR --json reviews --jq '.reviews | length')
    if [ "$NEW_REVIEWS" -gt "$PREV_REVIEWS" ]; then
        echo "ALERT:NEW_REVIEW"
        gh pr view $PR --json reviews --jq '.reviews[-1] | "@\(.author.login) (\(.state)): \(.body[0:500])"'
        exit 0
    fi

    # Merge conflicts
    MERGEABLE=$(gh pr view $PR --json mergeable --jq '.mergeable')
    if [ "$MERGEABLE" = "CONFLICTING" ]; then
        echo "ALERT:MERGE_CONFLICT"
        exit 0
    fi

    # PR closed/merged — stop monitoring
    STATE=$(gh pr view $PR --json state --jq '.state')
    if [ "$STATE" = "MERGED" ] || [ "$STATE" = "CLOSED" ]; then
        echo "INFO:PR_$STATE"
        exit 0
    fi
done
echo "INFO:TIMEOUT"
```

**Limitations:**
- If context gets compacted, the monitor notification may be lost. For most PRs the feedback loop is <30 minutes, well within a session.
- Only one monitor per session. If the agent opens multiple PRs, only the latest is monitored.
- The loop should cap at ~80 iterations (~2 hours). If no event occurs by then, exit with `INFO:TIMEOUT` so the process doesn't run indefinitely.

**Integration:** The monitor logic is documented as a reusable pattern in the skills, not a separate script file. Each skill (`/dev` Stage 7, `/finish-ticket` Phase 3, `/pr` new final step) includes the inline monitor launch.

**Files:** `/pr` skill, `/dev` skill, `/finish-ticket` skill, CLAUDE.md (remove broken self-chaining pattern)

## Cleanup

- Remove the broken self-chaining PR monitor pattern from `~/.claude/CLAUDE.md` (the "After Submitting a PR" section)
- The `/address-pr` and `/finish-ticket` skills remain separate — they serve different entry points (mid-review vs. post-implementation). No consolidation needed now.

## Out of Scope

- Hook-driven monitoring (B2 from brainstorming) — deferred as a future improvement if B1 proves insufficient
- Skill consolidation (`/finish-ticket` + `/address-pr`) — separate concern, track on Trello if desired
- Auto-merge — human gate stays
