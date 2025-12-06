# Objective: Dependency Injection for External Services

## Goal

Eliminate global state for external dependencies (especially EventEmitter) and make all external service access explicit through dependency injection. Functions that access external services should declare that dependency in their signature.

## Acceptance Criteria

1. **No global emitter** - `get_emitter()` and `record_event()` globals are removed
2. **EventEmitter on PolicyContext** - Policies access emitter via `ctx.emitter`
3. **Null Object implementations** - `NullEventEmitter` for tests (and optionally `NullRedisClient`, `NullDatabasePool`)
4. **`PolicyContext.for_testing()` factory** - Easy construction of test contexts with null implementations
5. **All existing tests pass** - No functional regressions
6. **Gateway uses injected emitter** - `gateway_routes.py` uses emitter from Dependencies, not global

## Out of Scope

- Changing how Redis/DB/LLM are injected at FastAPI route level (already works via Dependencies)
- Making `record_event` awaitable (fire-and-forget is acceptable)
