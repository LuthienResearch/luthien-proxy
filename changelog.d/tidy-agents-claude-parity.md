---
category: Chores & Docs
---

**AGENTS.md / CLAUDE.md parity**: Renamed all `CLAUDE.md` files to `AGENTS.md` and replaced each `CLAUDE.md` with a symlink pointing at its sibling `AGENTS.md`. A CI check (`.github/workflows/agents-parity.yml`) and pre-commit hook enforce that every `AGENTS.md` has a matching `CLAUDE.md` symlink so both names always resolve to the same content.
