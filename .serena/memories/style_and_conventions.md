# Code Style and Conventions

## Formatting
- **Line length**: 120 characters
- **Quotes**: Double quotes (`"`)
- **Indent**: Spaces (4)
- **Formatter**: Ruff (not black)
- **Import sorting**: isort via Ruff (`I` rules)

## Linting (Ruff)
- Rules enabled: E (pycodestyle), F (pyflakes), I (isort), D (pydocstyle/Google-style), PLC0415 (no function-level imports)
- E501 (line too long) is ignored — handled by formatter
- Tests exempt from D (docstrings) and PLC0415 (function-level imports)
- Migrations exempt from ALL rules

## Type Checking (Pyright)
- Mode: basic
- Scope: `src/` and `saas_infra/` only (NOT `tests/`)
- Python version: 3.13

## Docstrings
- **Google-style** (enforced via Ruff D rules, non-gating)

## Naming
- Standard Python: snake_case for functions/variables, PascalCase for classes
- f-strings ONLY — never `.format()` or `%`

## Testing
- pytest with `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed
- Unit tests have 3s timeout, network sockets blocked
- beartype smoke check in pre-commit

## Critical Conventions
- **`logging` only** — never `print()` for debugging
- **No function-level imports** (PLC0415 enforced, tests exempt)
- **`asyncio.Queue.shutdown()`** for stream termination, not `None` sentinels
- **Bounded queues** (maxsize=10000) with 30s timeout on put()
- **`beartype`** for runtime type checks in critical sections
- **`utils/`** has no `__init__.py` — import modules directly
- **Policy instances must be stateless** — mutable state → `PolicyContext.get_policy_state()`

## Anti-Patterns (NEVER DO)
- No `as any`, `type: ignore` without strong justification
- No empty catch blocks
- No print() for debugging
- No .format() or % formatting
- No None sentinels for queue termination
- No busy-wait with get_nowait() — use `await queue.get()`
- Don't overload fields — add new typed fields
- Content + finish_reason in same chunk — emit separately
- on_streaming_policy_complete() must NOT emit chunks
- Never modify applied migrations
