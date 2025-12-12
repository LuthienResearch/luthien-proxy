# Current Objective

Conversation context tracking across requests (luthien-proxy-5sr)

## Goal

Enable session ID tracking to group related API calls from Claude Code and other clients.

## Completed

- [x] Added `RawHttpRequest` dataclass to capture raw HTTP request data (previous session)
- [x] Updated `PolicyContext` to include `raw_http_request` field (previous session)
- [x] Added `session_id` field to `PolicyContext`
- [x] Created session ID extraction functions in `pipeline/session.py`:
  - `extract_session_id_from_anthropic_body`: Extracts from `metadata.user_id` (Claude Code format: `_session_<uuid>`)
  - `extract_session_id_from_headers`: Extracts from `x-session-id` header (OpenAI format)
- [x] Updated `_process_request` to extract session ID based on client format
- [x] Updated `process_llm_request` to pass session_id to PolicyContext
- [x] Added span attributes for session_id in OpenTelemetry tracing
- [x] Added unit tests for session ID extraction (12 tests, 100% coverage on session.py)
- [x] All existing tests updated and passing (20 processor tests + 12 session tests)
