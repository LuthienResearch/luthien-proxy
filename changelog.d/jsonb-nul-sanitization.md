---
category: Fixes
---

**Preserve observability payloads containing NUL characters**: Replace PostgreSQL-incompatible NUL characters with visible replacement characters before JSONB writes, and record the replacement count in stored payloads.
