# V2 E2E Tests Status

## Current State

The tests in this directory are **NOT true E2E tests**. They are **integration tests** that directly instantiate the `PolicyOrchestrator` (deprecated) and call it with a real LLM backend.

These tests currently import from `policy_orchestrator_old` which is deprecated.

## What True E2E Tests Should Do

True E2E tests should:
1. Start the FastAPI gateway server (or use TestClient)
2. Make HTTP requests to `/v1/chat/completions` or `/v1/messages`
3. Verify HTTP responses (status codes, headers, SSE format, content)
4. Test with different policies configured via app.state

## TODO

- [ ] Convert these to true E2E tests using httpx.AsyncClient or TestClient
- [ ] Add fixture to configure custom policies for testing
- [ ] Test gateway endpoints, not orchestrator directly
- [ ] OR move these to `tests/integration_tests/v2/` and create new E2E tests
