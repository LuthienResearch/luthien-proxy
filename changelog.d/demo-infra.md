---
category: Features
---

**Reproducible data-protection demo infrastructure**: Adds a deterministic demo of Luthien blocking a destructive tool call.
  - New `DemoForceBashRmRfPolicy` (DEMO ONLY) that fabricates a bash `rm -rf <target_path>` tool_use response on every request, parameterized by `target_path`.
  - Canonical demo project template at `dev/demo/template/` (slide deck draft + CSVs + a feedback inbox with a prompt-injection memo) — clone from this on each setup.
  - `scripts/demo_setup.sh` bootstraps the demo on a fresh machine: materializes the project dir, ensures the gateway is up, sets the policy chain.
  - `scripts/demo_toggle.sh` switches between `block` (safe demo — `MultiSerialPolicy(Demo, BlockDangerousCommands)`), `fail` (Demo alone — destructive call reaches the client), and `off` (NoOp).
  - `scripts/demo_reset.sh` wipes the demo project dir and re-clones from template — useful after a `fail` run actually deletes things.
  - Full setup + narration notes in `dev/demo/README.md`.
