---
category: Fixes
---

**Re-running `luthien onboard --docker` works on an existing install**: onboard wrote a new `POSTGRES_PASSWORD` on every run, but the Postgres volume keeps the password it was created with, so migrations failed with `password authentication failed` and the gateway never started. Onboard now starts the database first and sets its password to the one in `.env`, over the container's local socket
  - Works after switching from local mode back to Docker, and when `.env` was deleted
  - Docker onboard now also resets `POLICY_CONFIG` to the path inside the container, so a host path left by local mode no longer stops the gateway with `Policy config not found`
