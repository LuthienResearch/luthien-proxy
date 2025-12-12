# E2E Testing Guidelines

## Overview

E2E tests verify the gateway behavior by making real HTTP requests through the running infrastructure. These tests require:

- Gateway running (`docker compose up gateway`)
- Valid API credentials in `.env` or environment variables

## Running E2E Tests

```bash
# Run all e2e tests (slow - use sparingly)
uv run pytest -m e2e

# Run specific test file
uv run pytest tests/e2e_tests/test_session_tracking.py -m e2e -v
```

## Policy Management

### policy_context Helper

Use the `policy_context` async context manager to temporarily activate a policy during a test:

```python
from tests.e2e_tests.test_session_tracking import policy_context

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

The context manager:
1. Calls `POST /admin/policy/set` to activate the specified policy
2. Yields control to your test code
3. Restores `NoOpPolicy` in the `finally` block

### Admin API Endpoint

The policy management uses `POST /admin/policy/set` (not `/admin/policy/create` or `/admin/policy/activate`):

```python
response = await client.post(
    f"{gateway_url}/admin/policy/set",
    headers={"Authorization": f"Bearer {ADMIN_API_KEY}"},
    json={
        "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
        "config": {},
        "enabled_by": "e2e-test",
    },
)
```

## Test Structure

- Tests are marked with `@pytest.mark.e2e` to be excluded from fast unit test runs
- Use fixtures like `gateway_healthy`, `claude_available`, `codex_available` for prerequisite checks
- Tests that fail prerequisites are skipped, not failed

## CLI Testing

### Claude Code

```python
async def run_claude_code(prompt: str, timeout_seconds: int = 60):
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--max-turns", "1"]
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = GATEWAY_URL
    env["ANTHROPIC_AUTH_TOKEN"] = API_KEY
    # ...
```

### Codex

```python
async def run_codex(prompt: str, timeout_seconds: int = 60):
    cmd = ["codex", "exec", "--json", "-s", "read-only", "--skip-git-repo-check", prompt]
    env = os.environ.copy()
    env["OPENAI_BASE_URL"] = f"{GATEWAY_URL}/v1"
    env["OPENAI_API_KEY"] = API_KEY
    # ...
```
