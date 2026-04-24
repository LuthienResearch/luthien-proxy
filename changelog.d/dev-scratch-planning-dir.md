---
category: Chores & Docs
---

**Move planning scratch to `dev/scratch/` (gitignored)**: `dev/OBJECTIVE.md`, `dev/NOTES.md`, and in-flight design plans now live in gitignored `dev/scratch/` rather than tracked at `dev/` root. Objective Workflow updated: the objective-setting commit is now `git commit --allow-empty` (the message feeds `gh pr create --draft --fill`) so no artifact needs committing at the start. `dev/context/` and `dev/archive/` remain tracked as before. Motivation: prevent planning drafts from leaking into feature commits (e.g. commit 540e2825 bundled 1084 lines of scratch).
