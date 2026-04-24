---
category: Chores & Docs
---

**Move planning scratch to `dev/scratch/` (gitignored)**: `dev/OBJECTIVE.md`, `dev/NOTES.md`, and in-flight design plans now live in gitignored `dev/scratch/` rather than tracked at `dev/` root. Objective Workflow updated: first commit is the changelog stub, then `gh pr create --draft --fill` opens the PR. `dev/context/` and `dev/archive/` remain tracked as before. Motivation: prevent planning drafts from leaking into feature commits (e.g. commit 540e2825 bundled 1084 lines of scratch).
