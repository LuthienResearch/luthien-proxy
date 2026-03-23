---
category: Fixes
pr: 415
---

**Docker entrypoint SQLite fix**: Skip Postgres migrations when DATABASE_URL is a SQLite URL, and filter `sqlite_schema.sql` from the Postgres migration glob.
