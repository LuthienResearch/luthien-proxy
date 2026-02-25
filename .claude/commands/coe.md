# COE / Root Cause Analysis

For bug fix PRs, generate a COE (Correction of Errors) section for the PR description.

Every bug fix PR must include a COE in the PR description. Don't just fix the symptom; close the hole that let it through.

## Instructions

1. Analyze the current branch's changes against main:
   - Run `git diff main...HEAD` to see all changes
   - Run `git log main..HEAD --oneline` to see commit history
   - Read the modified files to understand context

2. Identify the bug being fixed: What was the user-visible or system-level symptom?

3. Write each of the following sections:

### Root Cause
What broke and why. Be specific about the code path — name the file, function, and the incorrect assumption or missing check. Explain the chain of events from trigger to symptom.

### Why It Wasn't Caught
What gap in process, testing, or architecture allowed this bug to reach production or main. Examples:
- Missing unit test for this edge case
- No integration test covering this interaction
- Type system couldn't catch this class of error
- Manual testing didn't cover this scenario
- Code review missed it because X

### Why It Won't Recur
What this fix changes **structurally** (not just "I fixed the line"). Include any preventive measures added:
- New tests that would catch regressions
- Type-level guarantees added
- Validation added at the boundary
- Architectural changes that eliminate the class of bug

### Preventive TODOs (if any)
List any follow-up items that would further prevent this class of bug but are out of scope for this PR. Add these to `dev/TODO.md` as well.

## Output Format

Output as markdown suitable for appending to a PR description, wrapped in a `## COE / Root Cause Analysis` section. Example structure:

```markdown
## COE / Root Cause Analysis

### Root Cause
[Specific description of what broke and why]

### Why It Wasn't Caught
[Gap in process/testing/architecture]

### Why It Won't Recur
[Structural changes, not just the fix]

### Preventive TODOs
- [ ] Follow-up item 1
- [ ] Follow-up item 2
```

## Reference Examples

These past COEs from the luthien-proxy repo demonstrate good RCA/COE practice:

- [PR #204](https://github.com/LuthienResearch/luthien-proxy/pull/204) — self-healing request pipeline (architectural fix for 5 instances of missing request validation)
- [PR #201](https://github.com/LuthienResearch/luthien-proxy/pull/201) — empty text content blocks causing API 400s
- [PR #178](https://github.com/LuthienResearch/luthien-proxy/pull/178) — cache_control sanitization gap after pipeline split
- [PR #167](https://github.com/LuthienResearch/luthien-proxy/pull/167) — orphaned tool_results after /compact
- [PR #134](https://github.com/LuthienResearch/luthien-proxy/pull/134) — thinking blocks in streaming (5-layer postmortem)
