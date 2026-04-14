---
category: Features
---

**Generic policy cache**: Added a DB-backed key-value cache that any policy can use for persistent, cross-request state. Policies access it via `context.policy_cache("MyPolicy")` and get isolated storage scoped by policy name. Entries have configurable TTL and survive restarts, unlike in-process caches. Works with both Postgres and SQLite deployments.
