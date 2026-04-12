---
category: Features
---

**Supply chain feed policy**: Block bash `tool_use` commands that install known-compromised package versions. Pulls from OSV's bulk GCS feed every 5 minutes, filters to CRITICAL advisories, and does O(1) dict lookup at request time. On hit, rewrites the command to a safe `exit 42` substitute.
  - Adds `on_policy_loaded(PolicyLoadContext)` lifecycle hook on `BasePolicy` for policies that need gateway services (db_pool, scheduler)
  - Dual-migrated `supply_chain_feed` + `supply_chain_feed_cursor` tables (Postgres + SQLite)
  - 55+ unit tests including captured-real-response fixtures from OSV and subprocess execution tests
