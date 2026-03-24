---
category: Fixes
pr: 422
---

**Single-pass dev_checks.sh**: Removed the pre-clean-tree check and auto-stages formatting fixes instead of failing. No more two-pass commit dance.
  - Script paths now use `git rev-parse --show-toplevel` for worktree compatibility
