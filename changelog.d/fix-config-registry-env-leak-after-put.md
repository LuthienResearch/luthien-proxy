---
category: Fixes
---

**Config DELETE no longer reverts to a ghost ENV layer after PUT**: `ConfigRegistry._resolve_field` used to re-derive the ENV layer on every resolve by comparing `settings.<field>` to `meta.default`. Because `set_db_value` writes the coerced value back into `Settings` via `_sync_one`, any field that received a DB override would be reported as ENV-sourced on later resolves, and a subsequent `DELETE /api/admin/config/{key}` would "stick" at the DB value instead of falling back to the real default. The ENV layer is now snapshotted once at `__init__` and consulted from that immutable map.
