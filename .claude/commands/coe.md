Write a COE (Correction of Errors) section for the current bug fix PR.

Every bug fix PR in this repo must include an RCA/COE in the PR description. Generate the following sections based on the changes in the current branch:

## Root Cause Analysis / COE

### 1. Root cause — what broke and why
Analyze the diff and explain what was broken and the underlying reason.

### 2. Why it wasn't caught — what gap in process/testing allowed it
Identify what testing, review, or process gap allowed this bug to reach the codebase.

### 3. Why it won't recur — what this fix changes structurally
Explain what structural change this PR makes (not just "I fixed the line") and any preventive measures.

### 4. Preventive TODOs (if any)
List any follow-up items that would further prevent similar issues. Add these to dev/TODO.md if appropriate.

---

Instructions:
1. Run `git log main..HEAD --oneline` and `git diff main...HEAD` to understand the changes
2. Read the modified files to understand the bug context
3. Draft the COE sections above
4. Output the COE as markdown that can be pasted into a PR description

Reference COE examples: see the "Bug fix PRs require RCA/COE" section in the project CLAUDE.md
