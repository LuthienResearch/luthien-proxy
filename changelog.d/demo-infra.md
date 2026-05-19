---
category: Features
---

**Reproducible Luthien demo infrastructure**: Adds a generic, manifest-driven demo harness and a first demo (`rm-rf`) showing Luthien blocking a destructive tool call.
  - Per-demo directories at `dev/demo/<name>/` with a `demo.toml` manifest, a `template/` workspace, and a `README.md` narration. New demos plug in without touching shell.
  - `scripts/demo_{setup,toggle,reset}.sh` take `<demo> [state] [surface]` (defaulting to `rm-rf block claude-code`). State = `block` (fabricator + protector) | `dontblock` (fabricator only) | `off` (NoOp). Surface = `claude-code` | `cowork` and picks the fabricated tool name.
  - First demo `rm-rf` includes `DemoForceBashRmRfPolicy` (fabricates a `rm -rf` bash tool_use; tool name configurable per surface) + `BlockDangerousCommandsPolicy` as the protector.
  - Top-level `dev/demo/README.md` covers: how to add a new demo, gateway prereqs, and how to point Claude Code or Cowork (third-party-inference config) at `http://localhost:8000`.
