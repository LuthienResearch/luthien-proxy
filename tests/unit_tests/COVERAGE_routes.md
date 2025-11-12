# Coverage Documentation: v2/routes.py

**Module:** `src/luthien_proxy/v2/routes.py`
**Coverage:** 0% (by design - covered by integration tests)

## Why Integration Testing?

This module is primarily covered by integration tests rather than unit tests.

### Rationale

- Contains FastAPI endpoints with complex async streaming logic
- Testing requires mocking FastAPI Request objects, app state, and LiteLLM
- Heavy mocking would create tests that diverge from actual behavior
- Integration tests provide better coverage by testing actual HTTP endpoints

### Integration Test Coverage

See: [tests/integration_tests/v2/test_routes.py](../../integration_tests/v2/test_routes.py)

Integration tests cover:
- OpenAI `/v2/chat/completions` endpoint (streaming and non-streaming)
- Anthropic `/v2/messages` endpoint (streaming and non-streaming)
- Health check endpoint
- Error handling
- Event publishing

## Potential Refactoring Opportunities

If these components were extracted as pure functions, they could be unit tested:

1. **`stream_llm_chunks()`** - Could be tested with mocked `litellm.acompletion`
2. **`hash_api_key()`** - Pure function, easily unit testable
3. **Event formatting logic** - If extracted from endpoints

These would be isomorphic refactors preserving current behavior.

## Adding Unit Tests

If specific helpers are extracted from routes.py that warrant unit tests, create:
- `tests/unit_tests/v2/test_routes_helpers.py`

Follow guidelines in [tests/unit_tests/CLAUDE.md](../CLAUDE.md)
