# E2E OWASP Scenarios — Session Summary (2026-03-23)

## Branch
`e2e-owasp-scenarios` → PR #408 (open, ready for review)

## What was built
- 5 new mock_e2e test files (48 tests): LLM01, LLM06, LLM08, gateway robustness, audit trail
- 7 real-API tests with retry + FailureCapture infrastructure
- `scripts/generate_mock_from_failures.py` — converts failure captures to mock regression tests
- `tests/e2e_tests/failure_registry/` — captures actual judge responses on test failure
- `mock-e2e` CI job in `dev-checks.yaml` — dockerless gateway, no Docker needed
- OWASP markers (llm01/02/04/06/07/08) in pyproject.toml

## Key shared helpers added to conftest.py
- `judge_pass()`, `judge_replace_text(replacement)` — mock judge responses
- `MOCK_HEADERS`, `BASE_REQUEST` — shared request constants
- `collect_sse_text(response)` — SSE streaming text collector
- `FailureCapture` class + `failure_capture` fixture — captures actual LLM responses on test failure
- `_reset_mock_server` — now gated on `mock_e2e` marker (doesn't start mock server for real-API tests)

## PR review fixes applied (2 rounds)
Round 1: API key leakage (.gitignore + strip api_key in FailureCapture), code duplication, weak assertions, autouse fixture, datetime timezone, generate_mock paths, audit trail polling, loose assertions, unused markers
Round 2: EOF newline, asyncio.get_event_loop() → time.monotonic(), SSE parsing extracted to collect_sse_text(), changelog category fix, scenario newline sanitization

## Known pre-existing failures (skipped)
- `test_mock_simple_llm_oauth_passthrough.py` (4) — PR #361 fixed, unskipped
- `test_mock_simple_llm_passthrough_auth.py` (3) — PR #361 fixed, unskipped  
- `test_mock_request_logs.py` (1) — PR #361 fixed, unskipped
- `test_mock_auth.py::test_admin_endpoint_rejects_regular_key` — PR #405 bug (b9a92809), Trello card https://trello.com/c/91UFNcH8, Slack message sent to Jai

## CI env vars needed for pytest process
```yaml
env:
  MOCK_ANTHROPIC_HOST: localhost
  ENABLE_REQUEST_LOGGING: "true"
```

## Key architectural insight: _instantiate_policy spread kwargs
`_instantiate_policy` in `config.py` uses spread kwargs: `{"config": {...}}` → `SimpleLLMPolicy(config={...})`.
The outer key IS the parameter name. Double-nested config in mock tests is CORRECT, not a bug.

## Test results at session end
- mock_e2e: 166 passed, 1 skipped, 0 failed
- real-API (e2e): 7 passed, 0 failed
- unit tests: 1464 passed

## Real API test fix: judge key resolution
`_JUDGE_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or dotenv_values(".env").get("ANTHROPIC_API_KEY", "")`
Needed because uv run doesn't override env vars already set in shell. Also must use flat config (not nested) for real API tests, and set `api_key` explicitly in policy config to bypass passthrough bearer token.
