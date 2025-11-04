# TODO

Items sourced from PR #46 reviews link back to originating comments for context.

## High Priority

- [ ] **Make policy selection easier for e2e testing** - Allow temporary policy specification without modifying config files:
  - Support policy override via request header (e.g., `X-Luthien-Policy: noop` or `X-Luthien-Policy: simple`)
  - Or environment variable override (e.g., `LUTHIEN_TEST_POLICY=noop`)
  - Or CLI flag for test runner (e.g., `pytest --policy=noop`)
  - Document testing patterns for different policies
  - Consider test fixtures that automatically swap policies
- [ ] **Policy API: Prevent common streaming mistakes** - Make it easier for policy writers to avoid streaming bugs:
  - Consider base class that auto-forwards chunks by default (opt-in buffering instead of opt-in forwarding)
  - Provide better helper functions for common patterns (send_content, send_blocked_message, etc.)
  - Add validation/warnings when policies don't forward chunks
  - Consider dependency injection for chunk creation to ensure correct types
- [ ] **Format blocked messages for readability** - Current blocked messages are ugly (backslashes, no newlines). Need to:
  - Pretty-print JSON tool arguments
  - Add proper line breaks in explanations
  - Consider terminal/web formatting differences
- [ ] **Review and document streaming infrastructure** - Comprehensive review of all streaming-related code for clarity and correctness:
  - **ToolCallJudgePolicy**: Flow from on_tool_call_delta → buffering → on_tool_call_complete → judging → blocking/allowing
  - **AnthropicSSEAssembler**: Conversion from OpenAI chunks to Anthropic SSE events, content block lifecycle
  - **StreamingChunkAssembler**: Chunk aggregation, block detection, state transitions
  - **StreamingOrchestrator**: Queue management, timeout handling, task coordination
  - **PolicyOrchestrator**: Integration between policy hooks and streaming pipeline
  - **Utils (create_text_chunk, create_tool_call_chunk)**: Ensure correct types throughout
  - Add diagrams or detailed comments explaining state machines and event flows
  - Document edge cases (incomplete tool calls, judge failures, timeout handling, etc.)
  - Ensure all helper methods have clear docstrings
  - Review and expand test coverage for all streaming components
- [ ] Add security documentation for dynamic policy loading mechanism (V2_POLICY_CONFIG) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Verify all environment variables are documented in README and .env.example ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Add max buffer size for chunk storage (synchronous_control_plane.py:220 - unbounded growth) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Review and test graceful shutdown behavior (ensure event publisher tasks complete) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add input validation: max request size and message count limits ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Move "v2" code out of v2/
- [ ] Add unit tests for all new pipeline code
- [ ] Consolidate/organize utility/helper functions (see policies/utils.py)
- [ ] move `litellm.drop_params = True` somewhere sensible
- [ ] thinking and verbosity model flags not respected
- [ ] write SimpleToolCallJudge policy for pedagogical purposes
- [ ] improve docstrings for SimplePolicy

## Medium Priority

- [ ] remove unnecessary string-matching test conditions (e.g. matching exception messages)
- [ ] call_id -> transaction_id
- [ ] Revisit ignored pyright issues
- [ ] Sort out PolicyContext/StreamingResponseContext
- [ ] Make filtering on the activity monitor easier and more intuitive
- [ ] Add rate limiting middleware (slowapi or custom FastAPI middleware) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Implement circuit breaker for upstream calls ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add Prometheus metrics endpoint ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Make streaming timeout configurable (currently hardcoded 30s) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Implement proper task tracking for event publisher (replace fire-and-forget) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add integration tests for error recovery paths ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Factor out env var logic into centralized config
- [ ] Add OpenAPI/Swagger documentation for V2 gateway ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Document production deployment best practices ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Add resource limits to docker-compose.yaml (mem_limit, cpus) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))

## Low Priority / Future Work

- [ ] 99% unit test coverage (currently 81%, focus on critical paths first) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add config schema validation (Pydantic model for v2_config.yaml) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add request/response size limits ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Implement adaptive timeout based on model type ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add policy composition (chaining multiple policies) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Expose database connection pooling configuration (pool size, timeout) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add cache headers to static files mount ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Standardize on docstrings (currently mixed ABOUTME/docstrings) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Extract magic numbers to named constants (timeouts, truncation lengths) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Consider stricter type checking (pyright "standard" or "strict" mode) ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445272764))
- [ ] Add health check endpoint with degraded state reporting ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Minimize type: ignore flags
- [ ] Load testing with multiple concurrent streams ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Memory leak detection for long-running streams ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
- [ ] Redis pub/sub performance testing under high event volume ([review](https://github.com/LuthienResearch/luthien-proxy/pull/46#issuecomment-3445270602))
