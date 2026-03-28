# Suggested Commands

## Daily Development
```bash
# Full dev checks (format + lint + type check + tests) — run before PR
./scripts/dev_checks.sh

# Quick format only
./scripts/format_all.sh

# Unit tests only (fast, network-blocked, 3s timeout)
uv run pytest tests/unit_tests

# Specific test file
uv run pytest tests/unit_tests/test_foo.py -v

# Integration tests (needs running Postgres)
uv run pytest tests/integration_tests

# E2E tests (slow, needs full stack)
uv run pytest -m e2e -x -v

# Mock E2E tests (no real API calls)
uv run pytest -m mock_e2e -x -v

# Type check
uv run pyright

# Lint only (no fix)
uv run ruff check src/ tests/

# Lint with fix
uv run ruff check --fix src/ tests/

# Format only
uv run ruff format src/ tests/
```

## Local Stack
```bash
# Start local stack (auto-port selection, builds from source)
./scripts/quick_start.sh

# Restart gateway after code changes (Docker mounts src/ read-only)
docker compose restart gateway

# View gateway logs
docker compose logs -f gateway

# Stop everything
docker compose down

# Full restart
docker compose down && ./scripts/quick_start.sh
```

## Testing & Debugging
```bash
# Test gateway health
curl http://localhost:8000/health

# Test gateway with API key
./scripts/test_gateway.sh

# Query debug logs from DB
uv run python scripts/query_debug_logs.py

# DB shell
uv run python scripts/psql.py
```

## Observability
```bash
# Start observability stack (Tempo)
./scripts/observability.sh up -d
```

## System Utils (macOS/Darwin)
```bash
git status / git diff / git log --oneline -20
ls -la
find . -name "*.py" -path "*/policies/*"
grep -r "pattern" src/
```
