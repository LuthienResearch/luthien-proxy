---
category: Features
---

**Server-side session search API**: The `/api/history/sessions` endpoint now
accepts `user`, `model`, `from`, `to`, `q`, and `policy_intervention` query
parameters for server-side filtering. Postgres uses a tsvector/GIN index for
full-text content search; SQLite uses a `json_tree` walk over message text.
