# TODO

## High Priority

### Core Features (User Story Aligned)

- [ ] **thinking and verbosity model flags not respected** - Model parameters like `thinking` and `verbosity` are not passed through to backend. Blocks User Story 1 (Solo Developer). Issue: `luthien-proxy-mfs` (P1).
- [ ] **Conversation history browser & export** - Enable users to browse and export full conversation logs from past sessions. Maps to `luthien-proxy-edl` (Conversation Viewer UI) in User Stories 1 & 2. Data already in `conversation_events` table. Could include: search by date, export to markdown/JSON, filter by user/session.

### Bugs

- [ ] **`/compact` fails with "Tool names must be unique" error** - When running Claude Code through Luthien, `/compact` returns: `API Error: 400 {"type":"error","error":{"type":"invalid_request_error","message":"tools: Tool names must be unique."}}`. Also saw 500 errors on retry. Works without Luthien. May be related to how Luthien handles/transforms tool definitions. Debug log: [Google Drive](https://drive.google.com/file/d/1Gn2QBZ2WqG6qY0kDK4KsgxJbmmuKRi1S/view?usp=drive_link) (see rows with "/compact ERROR" comments). PR: https://github.com/LuthienResearch/luthien-proxy/pull/112 Reference: Dogfooding session 2025-12-16.

### Pending Review

- [ ] **Review user-stories for priority adjustments** - Story 6 (Taylor/Junior Developer) added in PR #114. Review all stories and propose priority changes if needed. Reference: 2025-12-16.

### Policy UI & Admin

- [ ] **[Future] Smart dev key hint** - Only show clickable dev key hint when ADMIN_API_KEY matches default; otherwise just show "check .env or contact admin". Deferred as scope creep. Reference: dogfooding-login-ui-quick-fixes branch, 2025-12-15.
- [ ] **Activity Monitor missing auth indicator** - Gateway root page links to Activity Monitor but doesn't show "Auth Required" indicator for consistency with other protected pages. Reference: dogfooding session 2025-12-15.
- [ ] **[Future] Conversation history browser & export** - Enable users to browse and export full conversation logs from past sessions. Use case: Claude Code compacts conversations; user wants to recover detailed logs later. Could include: search by date, export to markdown/JSON, filter by user/session. Data already in `conversation_events` table. Reference: Dogfooding session 2025-12-15.

### Code Quality

- [ ] **Factor out common gateway route logic** - Extract duplicate pipeline setup from `/v1/chat/completions` and `/v1/messages`

### Documentation (High)

- [ ] Add security documentation for dynamic policy loading (POLICY_CONFIG)

### Security

- [ ] **Add input validation: max request size and message count limits** - Request size limit (10MB) exists, but no message count limit. Could allow unbounded message arrays.

## Medium Priority

### Dogfooding & UX

- [ ] **Retrospective on dogfooding sessions** - Review session CSVs in [Google Drive](https://drive.google.com/file/d/1YMd0CEgEF2vtvyAy70_SZQFFzp1ZG7C-/view?usp=drive_link) and document learnings. What worked? What was painful? What should we build next? **Deadline: Dec 23, 2025**.
- [ ] **"Logged by Luthien" indicator policy** - Create a simple policy that appends "logged and monitored by Luthien" to each response. Helps users know when they're going through the proxy vs direct API. Use case: Scott thought he was using Luthien but wasn't. Reference: Dogfooding session 2025-12-16.
- [ ] **Include tool calls in conversation_transcript** - Currently only text content is extracted. Adding tool calls would help with retros on unsafe tool calls (e.g., "what did Claude try to execute?"). Reference: Dogfooding session 2025-12-16.

### Code Improvements

- [ ] **SimplePolicy image support** - Add support for requests containing images in SimplePolicy. Currently `simple_on_request` receives text content only; needs to handle multimodal content blocks. (Niche use case - images pass through proxy correctly already)
- [ ] **Replace dict[str, Any] with ToolCallStreamBlock in ToolCallJudgePolicy** - Improve type safety for buffered tool calls
- [ ] **Policy API: Prevent common streaming mistakes** - Better base class defaults and helper functions
- [ ] **Format blocked messages for readability** - Pretty-print JSON, proper line breaks
- [ ] **Improve error handling for OpenTelemetry spans** - Add defensive checks when OTEL not configured (partial: `is_recording()` checks exist)

### Testing (Medium)

- [ ] **Add integration tests for error recovery paths** - DB failures, Redis failures, policy timeouts, network failures
- [ ] **Convert Loki validation scripts to e2e tests**
- [ ] **Audit tests for unjustified conditional logic**

### Infrastructure (Medium)

- [x] **Add migration tracking** - Implemented fail-fast validation in run-migrations.sh and gateway startup check. (bd: luthien-proxy-17j, PR #110)
- [ ] **DB Migration: call_id -> transaction_id** - Rename columns for consistency
- [ ] **Verify UI monitoring endpoints functionality** - Test all debug and activity endpoints
- [ ] Add rate limiting middleware
- [ ] Implement circuit breaker for upstream calls
- [ ] Add Prometheus metrics endpoint
- [x] Add resource limits to docker-compose.yaml

### Documentation (Medium)

- [ ] **Create visual database schema documentation** - Current `docs/database-schema.md` is basic markdown tables. Need a visual flow diagram showing data hierarchy from most-granular (`conversation_events`) up to human-readable (`conversation_transcript` view), with `SELECT * LIMIT 3` examples for each table. Reference: Dogfooding session 2025-12-16.
- [ ] Add OpenAPI/Swagger documentation for V2 gateway
- [ ] Document production deployment best practices
- [ ] Document timeout configuration rationale
- [ ] Document data retention policy

## Low Priority / Future Work

- [ ] **Simplify db.py abstractions** - Remove redundant protocol wrappers
- [ ] **Review observability stack** - Consolidate observability docs, verify Grafana/Loki integration
- [ ] Increase unit test coverage (currently ~78%)
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
