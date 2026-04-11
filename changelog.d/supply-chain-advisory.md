---
category: Features
---

**SupplyChainAdvisoryPolicy**: Best-effort CVE warning for cooperative LLMs.
  - Loose-regex extraction of `pip|uv|poetry|pipenv|conda|npm|yarn|pnpm|bun install/add` commands from outgoing Bash tool_use blocks and incoming tool_result text.
  - Concurrent OSV.dev lookups (capped by semaphore) with DB-backed caching of positive and negative results.
  - Injects an advisory text block alongside the original tool_use when any referenced package has a HIGH+ severity advisory; existing system prompt is preserved.
  - Threshold configurable via `advisory_severity_threshold` (default `HIGH`); `hard_block_versions` is reserved for a future release and rejected if set.
  - **Non-goals**: this is NOT a security boundary. It does not parse `sh -c`, chain operators, `eval`, base64, or any other obfuscation. Run OSV-Scanner inside the sandbox for hardened supply-chain defense.
