# E2E Testing Guidelines

## Overview

E2E tests verify the gateway behavior by making real HTTP requests through the running infrastructure. Tests are organized into three tiers with different markers and infrastructure requirements.

## Quick Start

```bash
# Run all available tiers (sqlite + mock; real requires ANTHROPIC_API_KEY)
./scripts/run_e2e.sh

# Run specific tiers
./scripts/run_e2e.sh sqlite       # In-process, no Docker
./scripts/run_e2e.sh mock         # In-process gateway + mock Anthropic server
./scripts/run_e2e.sh real         # Docker + real Anthropic API

# Pass extra pytest args after --
./scripts/run_e2e.sh mock -- -k "test_streaming" -vv
```

## Test Tiers

### `sqlite_e2e` — In-Process SQLite Tests

Spins up an in-process gateway with SQLite. No Docker, no external services.

```bash
./scripts/run_e2e.sh sqlite
# or directly:
uv run pytest -m sqlite_e2e tests/luthien_proxy/e2e_tests/sqlite/ -x -v --timeout=60
```

### `mock_e2e` — Mock Backend Tests

Runs an in-process gateway (`scripts/start_mock_gateway.py`) with a mock Anthropic server. Fast, deterministic, no API costs. The mock server is started automatically by the `mock_anthropic` session fixture in conftest.py.

```bash
./scripts/run_e2e.sh mock
# or directly (requires E2E_GATEWAY_URL, E2E_API_KEY, etc. — use run_e2e.sh):
uv run pytest -m mock_e2e tests/luthien_proxy/e2e_tests/ -x -v --timeout=30
```

### `e2e` — Real API Tests

Uses Docker Compose + real Anthropic API. Slower, costs money, tests real-world behavior.

```bash
./scripts/run_e2e.sh real
```

Some tests require specific gateway configurations:

| Tests | Requirement |
|-------|-------------|
| `test_claude_code.py` (judge tests) | `ANTHROPIC_API_KEY` in env |
| `test_streaming_chunk_structure.py` (judge test) | `ANTHROPIC_API_KEY` in env |

**Important:** sqlite_e2e and mock_e2e must run in **separate pytest sessions** to avoid module-level patching conflicts. `run_e2e.sh` handles this automatically.

## Shared Test Infrastructure

All shared fixtures and helpers are in `tests/luthien_proxy/e2e_tests/conftest.py`:

- **Fixtures**: `gateway_url`, `api_key`, `admin_api_key`, `auth_headers`, `admin_headers`, `gateway_healthy`, `mock_anthropic`, `mock_anthropic_port`, `claude_available`, `codex_available`
- **Helpers**: `set_policy()`, `get_current_policy()`, `policy_context()`, `auth_config_context()`

Fixtures are auto-discovered by pytest — no explicit import needed. The `sqlite/conftest.py` overrides `gateway_url`, `api_key`, and `admin_api_key` for the sqlite tier.

## Policy Management

### policy_context Helper

Use the `policy_context` async context manager to temporarily activate a policy during a test:

```python
@pytest.mark.mock_e2e
@pytest.mark.asyncio
async def test_with_custom_policy(mock_anthropic, gateway_url, admin_api_key):
    async with policy_context(
        "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy",
        {},  # config dict
        gateway_url=gateway_url,
        admin_api_key=admin_api_key,
    ):
        # Test code runs with DebugLoggingPolicy active
        ...
    # NoOpPolicy is automatically restored after the context exits
```

### Admin API Endpoint

The policy management uses `POST /api/admin/policy/set`:

```python
response = await client.post(
    f"{gateway_url}/api/admin/policy/set",
    headers={"Authorization": f"Bearer {admin_api_key}"},
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
- Use fixtures like `gateway_healthy`, `claude_available`, `codex_available` for prerequisite checks
- Tests that fail prerequisites are skipped, not failed

## Test Location Examples

- `mock_e2e` tests: `tests/luthien_proxy/e2e_tests/test_mock_*.py`
- `e2e` tests: `tests/luthien_proxy/e2e_tests/test_*.py`
- `sqlite_e2e` tests: `tests/luthien_proxy/e2e_tests/sqlite/test_*.py`

## CLI Testing

### Claude Code

```python
async def run_claude_code(prompt: str, gateway_url: str, api_key: str, timeout_seconds: int = 60):
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--max-turns", "1"]
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = gateway_url
    env["ANTHROPIC_API_KEY"] = api_key
    # ...
```
