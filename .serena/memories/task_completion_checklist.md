# Task Completion Checklist

## Before Marking Done
1. **Format**: `uv run ruff format src/ tests/` (or `./scripts/format_all.sh`)
2. **Lint**: `uv run ruff check src/ tests/` — fix any issues
3. **Type check**: `uv run pyright` — must pass on `src/`
4. **Unit tests**: `uv run pytest tests/unit_tests` — must pass, 3s timeout
5. **Integration tests** (if DB-related changes): `uv run pytest tests/integration_tests`

## Full Check (Before PR)
```bash
./scripts/dev_checks.sh
```
This runs format + lint + type check + tests in sequence.

## Key Rules
- **One PR = One Concern** — bug fix during feature work? Separate PR.
- **Streaming/non-streaming parity** — features must work identically in both paths
- **Never modify applied migrations** — hash validation catches drift
- If adding a policy: must implement both OpenAI hooks AND Anthropic execution interface
- Docker changes: `docker compose restart gateway` (not just restart, sometimes need recreate)
- Shell env overrides .env for API keys
