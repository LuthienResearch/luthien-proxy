---
category: Features
pr: 606
---

**Add `policy_type` registry table**: Catalog of available built-in policy types, decoupled from `current_policy`. Foundation for a future `policy_instance` table.
  - 18-entry explicit `REGISTERED_BUILTINS` allowlist in `policy_types.py`; templates and samples skipped.
  - `sync_policy_types()` is implemented and tested but not wired into the lifespan in this PR — wiring lands with `policy_instance`.
