---
category: Features
---

**Supply chain gate policy**: A new opt-in policy (`SupplyChainGatePolicy`) that
intercepts bash tool_use calls, detects package install commands via loose
regex, queries OSV.dev for known vulnerabilities, and rewrites the tool_use's
`command` field in place to fail loudly when a flagged package matches the
configured severity threshold. The cooperative LLM sees the failed command in
the next turn's tool_result and relays the CVE information to the user.

- Command-substitution intervention shape: the tool_use block keeps its
  original stream index so indices remain monotonic across the stream.
- Lockfile installs (`npm ci`, `pip install -r requirements.txt`, etc.) are
  substituted with a dry-run-only command so the LLM can review the resolved
  package list before re-invoking with explicit package names.
- Optional `explicit_blocklist` supports pinned versions that bypass OSV
  (e.g. `PyPI:litellm:1.59.0`).
- Best-effort gate for cooperative LLMs — explicitly NOT a security boundary.
  Use OSV-Scanner inside the execution sandbox for adversarial defense.
