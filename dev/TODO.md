# TODO

## High Priority

- [ ] Demo: DB drop
- [ ] Make data logging more efficient
- [ ] Unify response format passed to callbacks

## Medium Priority

- [ ] 99% unit test coverage
- [x] Why does the control plane docker container take ~5 seconds to restart?
- [ ] Simplify/reduce data passed from litellm to control plane
- [x] Add CI/CD step that runs Prisma migration validation for both control-plane and LiteLLM schemas
- [ ] Event logging architecture indexed by call_id, trace_id to replace the current debug logs system
- [ ] OpenTelemetry/Grafana/Loki for instrumentation/logging/debugging

## Low Priority

- [ ] Add composite DB index for frequent debug log queries [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Configure asyncpg pool command timeout settings [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Apply per-query timeouts to heavy debug log fetches [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Investigate circuit breaker guard for slow database operations [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Extract shared jsonblob parsing into `parse_data.py` for hooks/debug routes
- [ ] Maybe logging for bg tasks [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321954052)
- [ ] Actual type signatures on hook functions in litellm_callback
- [ ] Make callback module independent (include connection manager)
- [ ] Prefer (Type | None) to (Optional\[Type\]) throughout codebase
- [ ] Minimize :  ignore flags

### Performance (streamview, pr #16)

- [ ] Add pagination support for _fetch_trace_entries_by_trace for long conversations
- [ ] Implement cache layer for frequently accessed traces
- [ ] Add database indices on litellm_trace_id and post_time_ns if not present
- [x] Set reasonable limits on chunk buffer sizes to prevent memory exhaustion
- [ ] Implement cleanup mechanism for abandoned/stale traces in _stream_indices
- [ ] Add virtual scrolling or chunked rendering to conversation trace UIs to handle large histories smoothly [comment](https://github.com/LuthienResearch/luthien-proxy/pull/16#issuecomment-3340920605)

### Security (streamview, from pr #16)

- [x] Add rate limiting on SSE endpoints to prevent DoS
- [ ] Implement Content Security Policy headers for XSS prevention
- [ ] Add HTML entity escaping in conversation_view.js and conversation_by_trace.js
- [ ] Add maximum payload size limits for hook data
- [ ] Ensure proper sanitization of trace/call IDs to prevent injection attacks

### Reliability (streamview, from pr #16)

- [ ] Fix potential race condition in next_chunk_index with concurrent access (use locks/atomic ops)
- [ ] Guard conversation/snapshots.py _stream_indices against concurrent mutation (asyncio locks or task-local state) [comment](https://github.com/LuthienResearch/luthien-proxy/pull/16#issuecomment-3340920605)
- [ ] Add Redis connection pooling and reconnection logic for pub/sub
- [x] Ensure proper SSE stream cleanup on client disconnect (finally blocks)
- [ ] Add logging for extract_response_text exceptions instead of silent fallback
- [ ] Add circuit breaker or backoff guard for control-plane connection failures to prevent cascading outages [comment](https://github.com/LuthienResearch/luthien-proxy/pull/16#issuecomment-3340920605)
- [ ] Revisit chunk deletion logic in litellm_callback.py (176-180) to ensure limits don't drop in-progress chunks [comment](https://github.com/LuthienResearch/luthien-proxy/pull/16#issuecomment-3340920605)
- [ ] Bound exponential backoff attempt tracking in streams.py (89-92) so retry loops reset predictably [comment](https://github.com/LuthienResearch/luthien-proxy/pull/16#issuecomment-3340920605)

### Monitoring & Configuration (streamview, from pr #16)

- [ ] Add metrics for active SSE connections, event processing latency, Redis pub/sub health
- [ ] Make heartbeat interval configurable via environment variable (streams.py:75)
- [ ] Add metrics for hook post latencies, chunk processing rates, memory usage per trace
- [ ] Expose retry/backoff configuration (max attempts, ceiling) via environment variables for streaming reconnect logic [comment](https://github.com/LuthienResearch/luthien-proxy/pull/16#issuecomment-3340920605)

### Testing (streamview, from pr #16)

- [ ] Add integration tests for SSE streaming behavior (connection, heartbeat, reconnection)
- [ ] Add tests for Redis pub/sub failures
- [ ] Add tests for concurrent access to stream indices
- [ ] Add tests for frontend JavaScript functionality
- [ ] Add tests for database connection failures during trace fetching
- [ ] Add load tests for conversations with hundreds of calls/chunks

### Documentation (streamview, from pr #16)

- [x] Add architecture diagram showing data flow from proxy → control plane → UI
- [ ] Add docstrings to frontend JS functions for maintainability
- [ ] Write API documentation for new streaming and trace endpoints [comment](https://github.com/LuthienResearch/luthien-proxy/pull/16#issuecomment-3340920605)
- [ ] Document frontend components supporting streaming views (props, state transitions) [comment](https://github.com/LuthienResearch/luthien-proxy/pull/16#issuecomment-3340920605)
- [ ] Capture deployment considerations (Redis, SSE scaling, CSP requirements) [comment](https://github.com/LuthienResearch/luthien-proxy/pull/16#issuecomment-3340920605)
- [ ] Link `docs/dataflows.md` from README for discoverability
