---
category: Features
pr: 522
---

**Supply chain guard policy**: New `SupplyChainGuardPolicy` blocks package
installs with known vulnerabilities by querying OSV.dev.
  - Intercepts `Bash` tool calls for pip, npm, cargo, go, gem, and composer
    install commands (including `uv pip`, `yarn`, `pnpm`) in both streaming
    and non-streaming Anthropic responses.
  - Scans incoming `tool_result` blocks from prior installs and prepends a
    remediation warning to the system prompt when vulnerable packages were
    already installed.
  - OSV lookup results are persisted via `PolicyCache` so they survive
    restarts and are shared across workers.
  - Configurable severity threshold, allowlist, and fail-open/closed mode.
