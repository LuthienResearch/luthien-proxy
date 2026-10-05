---
category: Features
pr: 802
---

**Generate a policy from CLAUDE.md**: new `uv run python -m luthien_proxy.policy_generation.claude_md <path>` command extracts enforceable behavioral rules from an existing CLAUDE.md / AGENTS.md and emits a ready-to-load `SimpleLLMPolicy` YAML, with every rule tagged with its source line and the output round-trip validated through the policy loader
