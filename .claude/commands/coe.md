Write a COE (Correction of Errors) section for the current bug fix PR.

Every bug fix PR in this repo must include an RCA/COE in the PR description.

## Instructions

1. Run `git log main..HEAD --oneline` and `git diff main...HEAD` to understand the changes
2. Read the modified files to understand the bug context
3. Check prior COEs for patterns — run `gh pr list --state merged --search "RCA/COE" --limit 10` and review the reference examples at the bottom of this file
4. Draft ALL sections below — do not skip any
5. Output the COE as markdown ready to paste into the PR description

## Required COE Sections

### Bug: [one-line description]

**Impact:** Who is affected, how badly, and what's the blast radius? Think about the user's experience — not just "returns 400" but what that means for their workflow. Business impact: does this erode trust in the product?

**Repro Steps (before this fix):**
1. Check out commit `<hash>` or earlier
2. [Steps to reproduce]
3. Observe: [what goes wrong]

(Include commit hash so the bug is documented even after the fix merges to main.)

**Timeline:**

| Date | Event |
|------|-------|
| YYYY-MM-DD | Bug became possible (which commit/PR introduced it) |
| YYYY-MM-DD | Bug discovered (how — dogfooding, user report, test failure) |
| YYYY-MM-DD | Fix shipped (this PR) |

**5 Whys:**
1. Why did it break? ->
2. Why was [cause from #1] happening? ->
3. Why wasn't [cause from #2] handled? ->
4. Why wasn't [cause from #3] in place? ->
5. Why? -> (This should reach an architectural or process root cause, not just "the code was wrong")

**The Pattern:**

Search prior COEs (see reference examples below). Is this a new class of bug, or the Nth instance of an existing pattern?

| PR | Date | What went wrong | How discovered |
|----|------|----------------|----------------|
| This PR | Date | Description | How found |
| Prior related PRs if any | | | |

**Detection gap:**
1. How was the bug actually discovered?
2. How *should* it have been discovered? What automated mechanism (test, monitoring, lint) would catch this class of bug?

**What else could break? (Sweep)**

Search the codebase for the same pattern. Check for related assumptions that might also be wrong. Document what you checked:

| Search | Files checked | Result |
|--------|--------------|--------|
| [pattern searched] | [where] | [safe / found issue / fixed] |

**Class-level analysis:**

Is this bug an instance of a broader failure class? (e.g., "one streaming handler drops events" → "all streaming handlers may have ordering bugs")

If yes:
1. Name the failure class
2. List all instances found (not just this one)
3. Fix or document all instances — not just the one that triggered this COE

A COE that only patches the specific instance is incomplete. (Origin: [private-claude-code-docs PR #4](https://github.com/scottwofford/private-claude-code-docs/pull/4) — a gdrive read failure was fixed narrowly, missing 5 other MCP servers with the same class of problem.)

**Fixes Applied:**

| Issue | Fix | File |
|-------|-----|------|
| [what was wrong] | [what changed] | [where] |

**Action items:**

| Action | Owner | Due date | Type |
|--------|-------|----------|------|
| [action] | [who] | [when] | Architectural / Detection / Process |

(Every COE must have at least one action item beyond "fix the line." If the only action is the code fix itself, you haven't gone deep enough. Every action item must have an owner and due date — unowned items rot.)

**Completeness gate (answer before submitting):**

> "If a similar but slightly different version of this bug appeared tomorrow in an adjacent area, would this fix prevent it?"

If no → widen the COE scope. Go back to the class-level analysis and expand.

---

Reference examples (full PRs with COEs at this standard):
- https://github.com/LuthienResearch/luthien-proxy/pull/204 — design principles + self-healing pipeline (architectural fix for 5 instances of missing request validation)
- https://github.com/LuthienResearch/luthien-proxy/pull/134 — 5-layer postmortem, meta 5 Whys (thinking blocks in streaming)
- https://github.com/LuthienResearch/luthien-proxy/pull/356 — class-level sweep of streaming handlers (parallel tool_use ordering violation)
- https://github.com/LuthienResearch/luthien-proxy/pull/201 — empty text content blocks causing API 400s
- https://github.com/LuthienResearch/luthien-proxy/pull/203 — orphaned Docker containers from project name mismatch
- https://github.com/LuthienResearch/luthien-proxy/pull/202 — bash 3 compatibility for find-available-ports.sh
- https://github.com/scottwofford/private-claude-code-docs/pull/4 — class-level MCP audit after single-fix COE (the COE that led to adding class-level analysis)
