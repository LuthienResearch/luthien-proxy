---
category: Chores & Docs
pr: 391
---

**Dockerless default**: Documentation and `.env.example` now default to dockerless mode (SQLite, no Postgres/Redis) for development and single-user local use. Docker Compose with Postgres+Redis is positioned for multi-user production deployments. `quick_start.sh` now validates that DATABASE_URL isn't SQLite before starting Docker services.
