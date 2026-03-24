---
category: Features
pr: 424
---

**Parallel rules policy**: New `ParallelRulesPolicy` applies multiple text-rewriting rules in parallel via LLM calls, with automatic refinement when multiple rules modify the same content.

**CLAUDE.md rules policy**: New `ClaudeMdRulesPolicy` extracts objective rules from CLAUDE.md at session start, persists them to the database, and applies them on subsequent turns via `ParallelRulesPolicy`.
  - New `session_rules` database table for per-session rule persistence
  - `PolicyContext` now exposes `db_pool` for policies that need database access
