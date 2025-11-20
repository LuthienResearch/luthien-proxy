# Objective: Implement Dependency Injection

## Goal
Create a clean DI system with a central `Dependencies` container that gates all access to Redis, DB, and HTTP/LLM clients.

## Acceptance Criteria
- [ ] `Dependencies` dataclass in `src/luthien_proxy/dependencies.py`
- [ ] FastAPI `Depends()` functions for type-safe dependency access
- [ ] `LLMClient` instantiated once at startup (not per-request)
- [ ] All routes use `Depends()` instead of `getattr(app.state, ...)`
- [ ] `event_publisher` derived from `redis_client` (not stored separately)
- [ ] All tests pass
- [ ] Type checks pass
