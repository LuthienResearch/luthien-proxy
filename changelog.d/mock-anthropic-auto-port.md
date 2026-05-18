---
category: Fixes
---

**Mock Anthropic server auto-allocates a free port by default**: The
`mock_anthropic` test fixture now picks an unused TCP port from the OS
when `MOCK_ANTHROPIC_PORT` is unset, instead of always trying 18888.
This unblocks running e2e suites alongside other stacks (dev environments,
parallel CI shards) without manual port juggling. Set `MOCK_ANTHROPIC_PORT`
to a specific value to keep the old fixed-port behaviour.
