# Current Objective

**create_app dependency injection** (bd: luthien-proxy-sxa)

Accept db_pool and redis_client objects instead of URLs, enabling easier testing and more flexible configuration.

## Acceptance Criteria

- [x] `create_app()` accepts `db_pool: DatabasePool` and `redis_client: Redis` instead of URL strings
- [x] New `connect_db()` and `connect_redis()` helper functions handle URL→object conversion (raise on failure)
- [x] Resource lifecycle managed in `__main__` with proper try/finally cleanup
- [x] Tests pass mock objects directly without patching internals
- [x] All existing tests updated and passing
- [x] `./scripts/dev_checks.sh` passes
