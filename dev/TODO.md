# TODO

## High Priority

### Bugs

- [ ] **`/compact` fails with "Tool names must be unique" error** - When running Claude Code through Luthien, `/compact` returns: `API Error: 400 {"type":"error","error":{"type":"invalid_request_error","message":"tools: Tool names must be unique."}}`. Also saw 500 errors on retry. Works without Luthien. May be related to how Luthien handles/transforms tool definitions. Debug log: [Google Drive](https://drive.google.com/file/d/1Gn2QBZ2WqG6qY0kDK4KsgxJbmmuKRi1S/view?usp=drive_link). PR: [#112](https://github.com/LuthienResearch/luthien-proxy/pull/112). Reference: Dogfooding session 2025-12-16.
- [ ] **Thinking blocks stripped from non-streaming responses** - Causes 500 errors when `thinking` enabled. Fix `openai_to_anthropic_response()` to extract `message.thinking_blocks` and include FIRST in content array. [#128](https://github.com/LuthienResearch/luthien-proxy/issues/128). PR: [#131](https://github.com/LuthienResearch/luthien-proxy/pull/131).
- [x] **Thinking blocks not handled in streaming responses** - Fixed in PR #134. Required 5 debug cycles across 4 layers. [#129](https://github.com/LuthienResearch/luthien-proxy/issues/129)
- [ ] **Codex tool-call sequencing error (missing tool result messages)** - Codex errors: “assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'.” Repro with `trello mcp?` or even `hello` via `./scripts/launch_codex.sh`. PR: https://github.com/LuthienResearch/luthien-proxy/pull/166

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

- [ ] **Claude Code /resume is slow/buggy - Luthien opportunity** - Claude Code's native resume feature freezes/lags. Luthien could provide conversation history persistence that survives client restarts, enabling faster resume and cross-device continuity. [Trello](https://trello.com/c/GlT89gVw). Reference: Dogfooding 2026-01-24.
- [ ] **"Logged by Luthien" indicator policy** - Create a simple policy that appends "logged and monitored by Luthien" to each response. Helps users know when they're going through the proxy vs direct API. Use case: Scott thought he was using Luthien but wasn't. Reference: Dogfooding session 2025-12-16.
- [ ] **Capture git branch in database and expose in conversation history UI** - Store the current git branch when sessions are created (Claude Code sends this context). Display branch name in `/history` session list and detail views for easier cross-referencing with `/resume`. Reference: PR #133 UI work, 2026-01-23.
- [ ] **LLM-generated session titles** - Currently showing first user message as preview. Future: generate titles like "Auth module refactoring" using LLM call based on everything that happened (unique Luthien value-add vs Claude Code which only shows initial prompt). Needs storage decision (new column with migration, or cached). See [Session naming design](https://github.com/LuthienResearch/luthien-proxy/blob/main/dev/user-stories/06-junior-developer-learning-with-guardrails.md). Reference: PR #133 planning, 2026-01-23.

### Code Improvements

- [ ] **llm_format_utils.py: Replace defensive fallbacks with exceptions** - Several places silently mask errors instead of failing fast. Reference: refactoring session 2026-01-26.
  - `_convert_anthropic_image_block()` returns `None` for unknown source types - should raise
  - `_categorize_content_blocks()` silently skips non-dict blocks (`if not isinstance(block, dict): continue`) - should raise
  - `_convert_anthropic_message()` passes through unexpected content types (`if not isinstance(content, list)`) - should raise
  - `_convert_anthropic_message()` returns error as message content for unknown block types - should raise instead
  - `anthropic_to_openai_request()` defaults `messages` to `[]` via `.get()` but it's a required field - should use direct access
  - `_convert_anthropic_image_block()` defaults missing `data`/`url` to empty string - should raise for missing required fields

- [ ] **SimplePolicy image support** - Add support for requests containing images in SimplePolicy. Currently `simple_on_request` receives text content only; needs to handle multimodal content blocks. (Niche use case - images pass through proxy correctly already)

- [ ] **Replace dict[str, Any] with ToolCallStreamBlock in ToolCallJudgePolicy** - Improve type safety for buffered tool calls
- [ ] **Policy API: Prevent common streaming mistakes** - Better base class defaults and helper functions
- [ ] **Format blocked messages for readability** - Pretty-print JSON, proper line breaks
- [ ] **Improve error handling for OpenTelemetry spans** - Add defensive checks when OTEL not configured (partial: `is_recording()` checks exist)

### Testing (Medium)

- [ ] **Fix Claude Code E2E tests timing out** - `test_claude_code_*` tests timeout (120s). Root cause: Claude Code 2.x ignores `ANTHROPIC_BASE_URL` env var and sends requests directly to `api.anthropic.com` (uses OAuth). Need alternative routing method or mark tests as requiring special setup. Gateway works fine (curl passes). Other 93 E2E tests pass. Reference: 2026-01-25.
- [ ] **Expand E2E thinking block test coverage** - Basic streaming/non-streaming tests added in PR #134. Still needed: full test matrix covering streaming/non-streaming × single/multi-turn × with/without tools. The tools case would have caught the demo failure from COE #2. Reference: [PR #134](https://github.com/LuthienResearch/luthien-proxy/pull/134).
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
- [ ] Clean up temporary Codex session log in dev/conversation_logs/2026-02-01_codex-session-log.csv after external logging is restored

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
