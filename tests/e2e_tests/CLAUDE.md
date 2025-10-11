# E2E Test Infrastructure and Patterns

## Overview

E2E tests verify the complete request-response flow through the luthien-proxy system, including:
- LiteLLM proxy → Control plane → Policy → Client
- WebSocket streaming communication
- Docker container logs and instrumentation
- Callback invocation and data flow

## Shared Utilities

All shared test utilities are in `tests/e2e_tests/helpers/` and exported from `tests/e2e_tests/helpers/__init__.py`.

### Docker Logs (`helpers/docker_logs.py`)

Utilities for retrieving and parsing docker compose logs.

**Common Functions:**
```python
from tests.e2e_tests.helpers import get_litellm_logs, get_control_plane_logs

# Get recent logs from litellm-proxy container
logs = get_litellm_logs(since_seconds=10)

# Get recent logs from control-plane container
logs = get_control_plane_logs(since_seconds=10)

# Filter log lines by pattern
matching_lines = filter_logs_by_pattern(logs, "ENDPOINT START")

# Extract all stream IDs (UUIDs) from logs
stream_ids = extract_stream_ids(logs)

# Find the most recent log line matching a pattern
last_match = find_most_recent_match(logs, "WebSocket OUT")
```

**Key Details:**
- All functions automatically strip ANSI escape codes from logs
- Use `since_seconds` parameter to limit log retrieval window (default: 10s)
- Logs are returned as strings with `\\n`-separated lines

### HTTP Requests (`helpers/requests.py`)

Utilities for making API requests to the proxy.

**Common Functions:**
```python
from tests.e2e_tests.helpers import make_streaming_request, make_nonstreaming_request

# Make a streaming request and get all chunks
response, chunks = await make_streaming_request(
    model="dummy-agent",
    content="test message",
    api_key="sk-luthien-dev-key"
)

# Make a non-streaming request
response, data = await make_nonstreaming_request(
    model="dummy-agent",
    content="test message"
)
```

**Key Details:**
- All functions are async and return tuples of `(response, data)`
- Streaming requests automatically consume the entire SSE stream
- Default values: model="dummy-agent", api_key="sk-luthien-dev-key", base_url="http://localhost:4000"

### Callback Assertions (`helpers/callback_assertions.py`)

Utilities for inspecting callback invocations using the callback instrumentation system.

**Common Functions:**
```python
from tests.e2e_tests.helpers.callback_assertions import (
    assert_callback_was_called,
    assert_streaming_callback_yielded_chunks,
    get_callback_invocations,
    clear_callback_trace
)

# Assert a callback was invoked
assert_callback_was_called("async_post_call_streaming_iterator_hook", times=1)

# Assert streaming callback yielded expected number of chunks
chunks = assert_streaming_callback_yielded_chunks(
    "async_post_call_streaming_iterator_hook",
    min_chunks=1
)

# Get all invocations for inspection
invocations = get_callback_invocations("async_pre_call_hook")

# Clear trace before test
clear_callback_trace()
```

## Common Test Patterns

### Pattern 1: Log-Based Verification

Use this pattern when verifying that logging/instrumentation is working correctly.

```python
import pytest
from tests.e2e_tests.helpers import get_litellm_logs, filter_logs_by_pattern, make_streaming_request

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_websocket_messages_logged():
    # Make a request
    await make_streaming_request(content="test message")

    # Get logs and filter
    logs = get_litellm_logs(since_seconds=10)
    websocket_logs = filter_logs_by_pattern(logs, "WebSocket OUT")

    # Assert
    assert len(websocket_logs) > 0, "Expected WebSocket logs"
```

### Pattern 2: Callback Inspection

Use this pattern when verifying callback behavior and data flow.

```python
import pytest
from tests.e2e_tests.helpers import make_streaming_request
from tests.e2e_tests.helpers.callback_assertions import (
    assert_callback_was_called,
    assert_callback_received_arg,
    clear_callback_trace
)

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_callback_receives_request_data():
    clear_callback_trace()

    await make_streaming_request(model="dummy-agent")

    assert_callback_was_called("async_pre_call_hook", times=1)
    kwargs = assert_callback_received_arg("async_pre_call_hook", "kwargs")
    assert kwargs["model"] == "dummy-agent"
```

### Pattern 3: Stream ID Correlation

Use this pattern when verifying that logs for a single request share the same stream ID.

```python
import pytest
from tests.e2e_tests.helpers import get_control_plane_logs, extract_stream_ids, filter_logs_by_pattern, make_streaming_request

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_logs_share_stream_id():
    await make_streaming_request(content="unique test message")

    logs = get_control_plane_logs(since_seconds=5)

    # Find START log to get stream ID
    start_logs = filter_logs_by_pattern(logs, "ENDPOINT START")
    stream_ids = extract_stream_ids(start_logs[-1])  # Most recent
    stream_id = list(stream_ids)[0]

    # Verify all endpoint logs use same stream ID
    endpoint_logs = filter_logs_by_pattern(logs, "ENDPOINT")
    for log_line in endpoint_logs:
        assert stream_id in log_line
```

## Test Organization

### Naming Conventions
- Test files: `test_<feature>_e2e.py` or `test_<component>_logging.py`
- Test functions: `test_<specific_behavior>`
- Mark all E2E tests with `@pytest.mark.e2e`
- Mark async tests with `@pytest.mark.asyncio`

### File Structure
```
tests/e2e_tests/
├── CLAUDE.md                           # This file
├── helpers/
│   ├── __init__.py                    # Exports all utilities
│   ├── docker_logs.py                 # Log retrieval and parsing
│   ├── requests.py                    # HTTP request utilities
│   ├── callback_assertions.py         # Callback inspection
│   └── infra.py                       # Infrastructure management
├── test_callback_invocation.py        # Callback instrumentation tests
├── test_websocket_logging.py          # WebSocket logger tests
├── test_endpoint_logging.py           # Control plane endpoint tests
└── test_<feature>_e2e.py              # Feature-specific E2E tests
```

### Best Practices

1. **Use shared utilities** - Don't duplicate log retrieval or request code
2. **Use generous log windows** - `since_seconds=10` is usually sufficient; use 5s only for very targeted tests
3. **Filter logs carefully** - Use `filter_logs_by_pattern()` to isolate relevant log lines
4. **Clear callback trace** - Call `clear_callback_trace()` before tests that inspect callbacks
5. **Test one thing** - Each test should verify a single behavior
6. **Use descriptive messages** - Assertion messages should explain what was expected and what was found
7. **Avoid flakiness** - Use `since_seconds` windows large enough to capture logs even if timing varies

## Running E2E Tests

```bash
# Run all E2E tests
uv run pytest tests/e2e_tests/ -m e2e

# Run specific test file
uv run pytest tests/e2e_tests/test_endpoint_logging.py -m e2e

# Run specific test
uv run pytest tests/e2e_tests/test_endpoint_logging.py::test_endpoint_start_message_logged -m e2e

# Run with verbose output
uv run pytest tests/e2e_tests/ -m e2e -v

# Run with test output visible (useful for debugging)
uv run pytest tests/e2e_tests/ -m e2e -s
```

## Debugging Failed Tests

1. **Check docker logs manually**:
   ```bash
   docker compose logs litellm-proxy --tail 100 | grep "CALLBACK"
   docker compose logs control-plane --tail 100 | grep "ENDPOINT"
   ```

2. **Increase log window**:
   ```python
   logs = get_litellm_logs(since_seconds=30)  # Longer window
   ```

3. **Print logs in test**:
   ```python
   logs = get_litellm_logs(since_seconds=10)
   print(f"\\n\\nLogs:\\n{logs}\\n\\n")  # Use pytest -s to see output
   ```

4. **Check ANSI stripping**: All utilities automatically strip ANSI codes, but verify with:
   ```python
   import re
   ansi_pattern = re.compile(r"\x1b\[[0-9;]*m")
   assert not ansi_pattern.search(logs), "ANSI codes still present"
   ```

## Adding New Instrumentation Tests

When adding new logging/instrumentation (e.g., Step 4: Policy logging), follow this pattern:

1. **Add logger to the component** (e.g., `src/luthien_proxy/policies/policy_logger.py`)
2. **Create E2E test file** (e.g., `tests/e2e_tests/test_policy_logging.py`)
3. **Use shared utilities**:
   ```python
   from tests.e2e_tests.helpers import get_control_plane_logs, filter_logs_by_pattern, make_streaming_request
   ```
4. **Write tests for each log type** (START, CHUNK, END, ERROR, etc.)
5. **Test stream ID correlation** to verify logs can be traced
6. **Update this doc** if new patterns emerge

## Related Documentation

- [Pipeline Instrumentation Plan](../../dev/streaming_pipeline_instrumentation_plan.md) - Overall instrumentation roadmap
- [Developer Onboarding](../../docs/developer-onboarding.md) - Hook flows and streaming details
- [Diagrams](../../docs/diagrams.md) - Visual flow diagrams
- [Callback Instrumentation](../../src/luthien_proxy/proxy/callback_instrumentation.py) - Callback tracing implementation
- [WebSocket Logger](../../src/luthien_proxy/proxy/websocket_logger.py) - WebSocket message logging
- [Endpoint Logger](../../src/luthien_proxy/control_plane/endpoint_logger.py) - Control plane endpoint logging
