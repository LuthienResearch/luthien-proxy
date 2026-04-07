# Objective: MockAnthropicClient

Replace the HTTP-level mock backend (`MockAnthropicServer` + `ANTHROPIC_BASE_URL`) with a
Python-level `MockAnthropicClient` injected directly into the gateway's dependency container.

## Acceptance criteria

- [ ] `MockAnthropicClient` satisfies the `AnthropicClient` duck-type interface (`complete`, `stream`, `_base_url`)
- [ ] `MockAnthropicClient` exposes the same enqueue/inspect API as `MockAnthropicServer` (`enqueue`, `drain_queue`, `clear_requests`, `last_request`, `last_request_headers`)
- [ ] All four response types are supported: `MockResponse`, `MockErrorResponse`, `MockToolResponse`, `MockParallelToolResponse`
- [ ] mock_e2e tier runs via an in-process gateway (`mock/conftest.py`) — no subprocess, no port allocation
- [ ] All existing mock_e2e tests pass unchanged (test code is not modified)
- [ ] `run_e2e.sh mock` works without starting `start_mock_gateway.py`
