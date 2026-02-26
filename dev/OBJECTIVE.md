# Objective: Single-Container Docker Build

## Goal
Create a single Docker container that bundles the gateway, PostgreSQL, and Redis together with persistent storage, as a convenience/deployment option alongside the existing docker-compose setup.

## Acceptance Criteria
- [ ] Single Dockerfile that includes gateway, PostgreSQL, and Redis
- [ ] Process manager (supervisord) to manage all three services
- [ ] Persistent storage via volume mounts for Postgres and Redis data
- [ ] Auto-runs migrations on startup
- [ ] Respects existing env vars for configuration
- [ ] Exposes gateway port (default 8000)
- [ ] Documentation for building and running
- [ ] Existing docker-compose setup remains unchanged
