# TODO

## High Priority

- [ ] Demo: DB drop
- [ ] Implement conversation trace/event streaming refactor (see dev/conversation_trace_plan.md)

## Medium Priority

- [ ] 99% unit test coverage
- [ ] Why does the control plane docker container take ~5 seconds to restart?

## Medium Priority - Stream View Improvements

### Performance
- [ ] Add pagination support for _fetch_trace_entries_by_trace for long conversations
- [ ] Implement cache layer for frequently accessed traces
- [ ] Add database indices on litellm_trace_id and post_time_ns if not present
- [ ] Set reasonable limits on chunk buffer sizes to prevent memory exhaustion
- [ ] Implement cleanup mechanism for abandoned/stale traces in _stream_indices

### Security
- [ ] Add rate limiting on SSE endpoints to prevent DoS
- [ ] Implement Content Security Policy headers for XSS prevention
- [ ] Add HTML entity escaping in conversation_view.js and conversation_by_trace.js
- [ ] Add maximum payload size limits for hook data
- [ ] Ensure proper sanitization of trace/call IDs to prevent injection attacks

### Reliability
- [ ] Fix potential race condition in next_chunk_index with concurrent access (use locks/atomic ops)
- [ ] Add Redis connection pooling and reconnection logic for pub/sub
- [ ] Ensure proper SSE stream cleanup on client disconnect (finally blocks)
- [ ] Add logging for extract_response_text exceptions instead of silent fallback

### Monitoring & Configuration
- [ ] Add metrics for active SSE connections, event processing latency, Redis pub/sub health
- [ ] Make heartbeat interval configurable via environment variable (streams.py:75)
- [ ] Add metrics for hook post latencies, chunk processing rates, memory usage per trace

### Testing
- [ ] Add integration tests for SSE streaming behavior (connection, heartbeat, reconnection)
- [ ] Add tests for Redis pub/sub failures
- [ ] Add tests for concurrent access to stream indices
- [ ] Add tests for frontend JavaScript functionality
- [ ] Add tests for database connection failures during trace fetching
- [ ] Add load tests for conversations with hundreds of calls/chunks

### Documentation
- [ ] Add architecture diagram showing data flow from proxy → control plane → UI
- [ ] Add docstrings to frontend JS functions for maintainability

## Low Priority

- [ ] Add composite DB index for frequent debug log queries [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Configure asyncpg pool command timeout settings [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Apply per-query timeouts to heavy debug log fetches [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Investigate circuit breaker guard for slow database operations [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321937242)
- [ ] Extract shared jsonblob parsing into `parse_data.py` for hooks/debug routes
- [ ] Maybe logging for bg tasks [comment](https://github.com/LuthienResearch/luthien-proxy/pull/13#issuecomment-3321954052)
