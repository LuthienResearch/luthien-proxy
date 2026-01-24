# TODO

## High Priority

### Bugs

- [ ] **`/compact` fails with "Tool names must be unique" error** - When running Claude Code through Luthien, `/compact` returns: `API Error: 400 {"type":"error","error":{"type":"invalid_request_error","message":"tools: Tool names must be unique."}}`. Also saw 500 errors on retry. Works without Luthien. May be related to how Luthien handles/transforms tool definitions. Debug log: [Google Drive](https://drive.google.com/file/d/1Gn2QBZ2WqG6qY0kDK4KsgxJbmmuKRi1S/view?usp=drive_link). PR: [#112](https://github.com/LuthienResearch/luthien-proxy/pull/112). Reference: Dogfooding session 2025-12-16.
- [ ] **Thinking blocks stripped from non-streaming responses** - Causes 500 errors when `thinking` enabled. Fix `openai_to_anthropic_response()` to extract `message.thinking_blocks` and include FIRST in content array. [#128](https://github.com/LuthienResearch/luthien-proxy/issues/128). PR: [#131](https://github.com/LuthienResearch/luthien-proxy/pull/131).
- [ ] **Thinking blocks not handled in streaming responses** - `AnthropicSSEAssembler` doesn't recognize thinking chunk types. [#129](https://github.com/LuthienResearch/luthien-proxy/issues/129)

### Core Features (User Story Aligned)

- [ ] **Conversation history browser & export** - Enable users to browse and export full conversation logs from past sessions. Maps to `luthien-proxy-edl` (Conversation Viewer UI) in User Stories 1 & 2. Data already in `conversation_events` table. Could include: search by date, export to markdown/JSON, filter by user/session.

### Policy UI & Admin

- [ ] **Improved policy config schema system** - Enhance config schema to include: default values, per-field user-facing docstrings/descriptions, and secure field flags (e.g. API keys should render as password inputs in browser). Currently the UI infers types from schema but lacks rich metadata for good UX.
- [ ] **[Future] Smart dev key hint** - Only show clickable dev key hint when ADMIN_API_KEY matches default; otherwise just show "check .env or contact admin". Deferred as scope creep. Reference: dogfooding-login-ui-quick-fixes branch, 2025-12-15.
- [ ] **Activity Monitor missing auth indicator** - Gateway root page links to Activity Monitor but doesn't show "Auth Required" indicator for consistency with other protected pages. Reference: dogfooding session 2025-12-15.

### Documentation (High)

- [ ] **Add security documentation for dynamic policy loading (POLICY_CONFIG)** - Document security implications of dynamic class loading, file permissions, admin API authentication requirements.

### Security

- [ ] **Add input validation: max request size and message count limits** - Request size limit (10MB) exists, but no message count limit. Could allow unbounded message arrays.

## Medium Priority

### Dogfooding & UX

- [ ] **"Logged by Luthien" indicator policy** - Create a simple policy that appends "logged and monitored by Luthien" to each response. Helps users know when they're going through the proxy vs direct API. Use case: Scott thought he was using Luthien but wasn't. Reference: Dogfooding session 2025-12-16.
- [ ] **Capture git branch in database and expose in conversation history UI** - Store the current git branch when sessions are created (Claude Code sends this context). Display branch name in `/history` session list and detail views for easier cross-referencing with `/resume`. Reference: PR #133 UI work, 2026-01-23.
- [ ] **LLM-generated session titles** - Currently showing first user message as preview. Future: generate titles like "Auth module refactoring" using LLM call based on everything that happened (unique Luthien value-add vs Claude Code which only shows initial prompt). Needs storage decision (new column with migration, or cached). See [Session naming design](https://github.com/LuthienResearch/luthien-proxy/blob/main/dev/user-stories/06-junior-developer-learning-with-guardrails.md). Reference: PR #133 planning, 2026-01-23.

### Code Improvements

- [ ] **SimplePolicy image support** - Add support for requests containing images in SimplePolicy. Currently `simple_on_request` receives text content only; needs to handle multimodal content blocks. (Niche use case - images pass through proxy correctly already)

- [ ] **Replace dict[str, Any] with ToolCallStreamBlock in ToolCallJudgePolicy** - Improve type safety for buffered tool calls
- [ ] **Policy API: Prevent common streaming mistakes** - Better base class defaults and helper functions
- [ ] **Format blocked messages for readability** - Pretty-print JSON, proper line breaks
- [ ] **Improve error handling for OpenTelemetry spans** - Add defensive checks when OTEL not configured (partial: `is_recording()` checks exist)

### Testing (Medium)

- [ ] **Add integration tests for error recovery paths** - DB failures, Redis failures, policy timeouts, network failures
- [ ] **Audit tests for unjustified conditional logic**

### Infrastructure (Medium)

- [ ] **Verify UI monitoring endpoints functionality** - Test all debug and activity endpoints (debug endpoints have tests, UI routes do not)
- [ ] **Add rate limiting middleware** - Not blocking any user story, but useful for production
- [ ] **Implement circuit breaker for upstream calls** - Queue overflow protection exists, but not full circuit breaker pattern

### Documentation (Medium)

- [ ] **Create visual database schema documentation** - Current `docs/database-schema.md` is basic markdown tables. Need a visual flow diagram showing data hierarchy from most-granular (`conversation_events`) up to human-readable (`conversation_transcript` view), with `SELECT * LIMIT 3` examples for each table. Reference: Dogfooding session 2025-12-16.
- [ ] Add OpenAPI/Swagger documentation for V2 gateway
- [ ] Document production deployment best practices
- [ ] Document timeout configuration rationale
- [ ] Document data retention policy

## Low Priority / Future Work

- [ ] **Simplify db.py abstractions** - Remove redundant protocol wrappers
- [ ] **Review observability stack** - Consolidate observability docs, verify Grafana/Loki integration
- [ ] Increase unit test coverage (currently ~90%, target 95%+)
- [ ] Add config schema validation (Pydantic model for policy_config.yaml)
- [ ] Implement adaptive timeout based on model type
- [ ] Add policy composition (chaining multiple policies)
- [ ] Expose database connection pooling configuration
- [ ] Add cache headers to static files mount
- [ ] Consider stricter pyright mode
- [ ] Add degraded state reporting to /health endpoint
- [ ] Minimize type: ignore flags
- [ ] Load testing with multiple concurrent streams
- [ ] Memory leak detection for long-running streams
- [ ] Redis pub/sub performance testing under high event volume
