---
category: Features
pr: 544
---

**Supply-chain blocklist policy**: New best-effort gateway policy that blocks bash tool_use install commands for known-compromised package versions via a background OSV poller and in-memory lookup.
  - Adds dual migrations for `supply_chain_blocklist` + `supply_chain_blocklist_cursor` tables.
  - Request-time path is a tiny in-memory lookup (PEP 440 / semver range matching + literal substring backstop) — no OSV traffic in the hot path.
  - Substitution is an `sh -c '... LUTHIEN BLOCKED ... exit 42'` rewrite of `tool_use.input.command`, same block index, no new content blocks.
  - Depends on `worktree-policy-scheduler` (PR A) for the real scheduler primitive; this PR stubs `SchedulerProtocol` and a `register_scheduled_tasks` hook.
  - Cooperative-LLM only. Not a security boundary against adversarial obfuscation. Lockfile installs explicitly out of scope — use OSV-Scanner in CI.
