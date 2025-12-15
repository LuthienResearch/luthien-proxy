# Current Objective

**create_app dependency injection** (bd: luthien-proxy-sxa)

Accept db_pool and redis_client objects instead of URLs, enabling easier testing and more flexible configuration.

## Acceptance Criteria

- [x] `create_app()` accepts `db_pool: DatabasePool | None` and `redis_client: Redis | None` instead of URL strings
- [x] New `connect_db()` and `connect_redis()` helper functions handle URL→object conversion
- [x] New `get_app()` async function composes everything for production use
- [x] Tests pass mock objects directly without patching internals
- [x] All existing tests updated and passing
- [x] `./scripts/dev_checks.sh` passes
