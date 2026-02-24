Write a COE (Correction of Errors) section for the current bug fix PR.

Every bug fix PR in this repo must include an RCA/COE in the PR description.

## Instructions

1. Run `git log main..HEAD --oneline` and `git diff main...HEAD` to understand the changes
2. Read the modified files to understand the bug context
3. Check prior COEs for patterns — run `gh pr list --state merged --search "RCA/COE" --limit 10` and review recent bug fix PRs listed in the "Bug fix PRs require RCA/COE" section of CLAUDE.md
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

Search prior COEs (CLAUDE.md has the list). Is this a new class of bug, or the Nth instance of an existing pattern?

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

**Fixes Applied:**

| Issue | Fix | File |
|-------|-----|------|
| [what was wrong] | [what changed] | [where] |

**Action items:**

| Action | Owner | Due date | Type |
|--------|-------|----------|------|
| [action] | [who] | [when] | Architectural / Detection / Process |

(Every COE must have at least one action item beyond "fix the line." If the only action is the code fix itself, you haven't gone deep enough.)

---

Reference examples (full PRs with COEs at this standard):
- https://github.com/LuthienResearch/luthien-proxy/pull/201
- https://github.com/LuthienResearch/luthien-proxy/pull/203
- https://github.com/LuthienResearch/luthien-proxy/pull/202
