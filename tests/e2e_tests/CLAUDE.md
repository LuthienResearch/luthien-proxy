# E2E Testing Guidelines

## Overview

E2E tests verify the gateway behavior by making real HTTP requests through the running infrastructure. Tests are organized into three categories with different markers and infrastructure requirements.

## Test Categories

### `mock_e2e` — Mock Backend Tests

Use a mock Anthropic server (port 18888) instead of real API calls. Fast, deterministic, no API costs.

**Setup:**
```bash
docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d
```

**Run:**
```bash
uv run pytest -m mock_e2e tests/e2e_tests/ -x -v
```

The mock server is started automatically by the `mock_anthropic` fixture in conftest.py. The `docker-compose.mock-bridge.yaml` overlay points the gateway at `host.docker.internal:18888`.

### `e2e` — Real API Tests

Use the real Anthropic API. Slower, costs money, tests real-world behavior.

**Setup:**
```bash
docker compose up -d  # Standard gateway with real API keys
```

**Run:**
```bash
uv run pytest -m "e2e and not mock_e2e and not sqlite_e2e" tests/e2e_tests/ -x -v
```

Some tests require specific gateway configurations:

| Tests | Requirement | Setup |
|-------|-------------|-------|
| `test_policy_composition.py` (dogfood tests) | `DOGFOOD_MODE=true` | Compose override with `DOGFOOD_MODE=true` |
| `test_request_logging.py` | `ENABLE_REQUEST_LOGGING=true` | Override + set `E2E_GATEWAY_URL=http://localhost:8000` |
| `test_claude_code.py` (judge tests) | `ANTHROPIC_API_KEY` in env | Loaded from `.env` by conftest |
| `test_streaming_chunk_structure.py` (judge test) | `ANTHROPIC_API_KEY` in env | Loaded from `.env` by conftest |

### `sqlite_e2e` — In-Process SQLite Tests

Run an in-process gateway with SQLite — no Docker needed. Fast and self-contained.

**Run:**
```bash
uv run pytest -m sqlite_e2e tests/e2e_tests/sqlite/ -x -v
```

**Important:** sqlite_e2e and mock_e2e must run in **separate pytest sessions** to avoid module-level constant contamination from the sqlite conftest's patching.

## Running All E2E Tests

To run every e2e test with proper server setup:

```bash
# 1. SQLite tests (no Docker needed, run FIRST in own session)
uv run pytest -m sqlite_e2e tests/e2e_tests/sqlite/ -x -v --timeout=60

# 2. Mock e2e tests (separate session)
docker compose -f docker-compose.yaml -f docker-compose.mock-bridge.yaml up -d
uv run pytest -m mock_e2e tests/e2e_tests/ -x -v --timeout=120

# 3. Real e2e tests (standard config)
docker compose up -d gateway
uv run pytest -m "e2e and not mock_e2e and not sqlite_e2e" tests/e2e_tests/ -x -v --timeout=120 \
  -k "not test_request_logging"

# 4. Dogfood mode tests (compose override)
# Create override: services.gateway.environment: [DOGFOOD_MODE=true]
docker compose -f docker-compose.yaml -f /tmp/compose-dogfood.yaml up -d gateway
uv run pytest -m e2e tests/e2e_tests/test_policy_composition.py -v --timeout=120

# 5. Request logging tests (compose override)
# Create override: services.gateway.environment: [ENABLE_REQUEST_LOGGING=true]
docker compose -f docker-compose.yaml -f /tmp/compose-reqlog.yaml up -d gateway
E2E_GATEWAY_URL=http://localhost:8000 uv run pytest -m e2e tests/e2e_tests/test_request_logging.py -v --timeout=120

# 6. Restore gateway to normal
docker compose up -d gateway
```

## Shared Test Infrastructure

All shared fixtures and helpers are in `tests/e2e_tests/conftest.py`:

- **Fixtures**: `claude_available`, `gateway_healthy`, `http_client`
- **Configuration**: `GATEWAY_URL`, `API_KEY`, `ADMIN_API_KEY`
- **Helpers**: `set_policy()`, `get_current_policy()`, `policy_context()`

These are auto-discovered by pytest - no explicit import needed for fixtures.

## Policy Management

### policy_context Helper

Use the `policy_context` async context manager to temporarily activate a policy during a test:

```python
from tests.e2e_tests.conftest import policy_context

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_with_custom_policy():
    async with policy_context(
        "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy",
        {},  # config dict
    ):
        # Test code runs with DebugLoggingPolicy active
        result = await make_request()
        assert result.success
    # NoOpPolicy is automatically restored after the context exits
```

### Admin API Endpoint

The policy management uses `POST /api/admin/policy/set`:

```python
response = await client.post(
    f"{gateway_url}/api/admin/policy/set",
    headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    json={
        "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
        "config": {},
        "enabled_by": "e2e-test",
    },
)
```

## Test Structure

- `mock_e2e` tests use `pytestmark = pytest.mark.mock_e2e` at module level
- `e2e` tests use `@pytest.mark.e2e` on individual test functions
- `sqlite_e2e` tests use `pytestmark = pytest.mark.sqlite_e2e` at module level
- Tests that fail prerequisites are skipped, not failed

## CLI Testing

### Claude Code

```python
async def run_claude_code(prompt: str, timeout_seconds: int = 60):
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--max-turns", "1"]
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = GATEWAY_URL
    env["ANTHROPIC_API_KEY"] = API_KEY
    # ...
```
