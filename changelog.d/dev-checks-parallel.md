---
category: Chores & Docs
---

**dev_checks: concurrent pyright + pytest**: In Phase 2, pyright and pytest now run in parallel (they're independent). Output is captured to separate logs and surfaced sequentially after both complete. Saves ~9-10s on a typical warm run (~54s → ~44s).
