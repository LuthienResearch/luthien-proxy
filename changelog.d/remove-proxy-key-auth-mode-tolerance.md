---
category: Chores & Docs
---

**Remove `AUTH_MODE=proxy_key` legacy tolerance**: The rename landed in #535 with tolerance code tagged `TODO(post-v0.2): remove`. v0.2 is not shipping, so the tolerance is gone now: `parse_auth_mode()` and its aliases dict, the `_coerce_legacy_auth_mode` Settings validator, the `_read_env_file_value` helper, the leftover-`PROXY_API_KEY` warning, and the `--auth-mode proxy_key` CLI pre-coercion. Adds migration 014 to set `auth_config.auth_mode` default to `both` (Postgres `ALTER`, SQLite table-swap) so a future INSERT without an explicit `auth_mode` can't resurrect the invalid `proxy_key` value and crash-loop the gateway.
