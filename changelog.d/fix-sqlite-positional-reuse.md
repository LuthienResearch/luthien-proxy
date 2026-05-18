---
category: Fixes
pr: 600
---

**Fix SQLite translator mishandling of positional arg reuse**: queries that reuse
a single `$N` placeholder (e.g. `VALUES (..., $8, $8)`) — valid on
asyncpg/Postgres — now work against the SQLite backend. Unblocks
`POST /api/admin/credentials` and any future positional-reuse call sites on
dockerless dev setups.
