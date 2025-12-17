# Database Schema

This document describes the Luthien proxy database schema for conversation tracking and debugging.

## Overview

Luthien stores conversation data in PostgreSQL for debugging, observability, and compliance purposes.

## Tables

### `conversation_calls`
One row per API call through the proxy.

| Column | Type | Description |
|--------|------|-------------|
| `call_id` | TEXT (PK) | Unique identifier for the API call |
| `model_name` | TEXT | Model requested (e.g., claude-sonnet-4-5) |
| `provider` | TEXT | LLM provider |
| `status` | TEXT | Call status (started, success, etc.) |
| `created_at` | TIMESTAMPTZ | When the call started |
| `completed_at` | TIMESTAMPTZ | When the call completed |

### `conversation_events`
Raw events for each call. Contains full JSON payloads.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Event unique identifier |
| `call_id` | TEXT (FK) | References conversation_calls |
| `event_type` | TEXT | Event type (see below) |
| `payload` | JSONB | Full event data |
| `created_at` | TIMESTAMPTZ | Event timestamp |
| `session_id` | TEXT | Session identifier (may be NULL) |

**Event Types:**
- `pipeline.client_request` - Incoming request from client
- `pipeline.format_conversion` - Format conversion (Anthropic <-> OpenAI)
- `transaction.request_recorded` - Final request sent to LLM
- `pipeline.backend_request` - Request to backend LLM
- `transaction.streaming_response_recorded` - Streaming response completed
- `transaction.non_streaming_response_recorded` - Non-streaming response

### `policy_events`
Policy decisions and modifications.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Event unique identifier |
| `call_id` | TEXT (FK) | References conversation_calls |
| `policy_class` | TEXT | Policy class name |
| `policy_config` | JSONB | Policy configuration |
| `event_type` | TEXT | Policy event type |
| `metadata` | JSONB | Additional metadata |
| `created_at` | TIMESTAMPTZ | Event timestamp |

### `conversation_judge_decisions`
LLM judge policy decisions.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Decision unique identifier |
| `call_id` | TEXT (FK) | References conversation_calls |
| `probability` | DOUBLE | Judge probability score |
| `explanation` | TEXT | Judge explanation |
| `tool_call` | JSONB | Tool call being judged |
| `created_at` | TIMESTAMPTZ | Decision timestamp |

## Views

### `conversation_transcript`
Human-readable conversation log. Use this for debugging and session review instead of raw `conversation_events`.

| Column | Type | Description |
|--------|------|-------------|
| `session_id` | TEXT | Session identifier (may be NULL) |
| `created_at` | TIMESTAMPTZ | Event timestamp |
| `prompt_or_response` | TEXT | `PROMPT` or `RESPONSE` |
| `model` | TEXT | Model name |
| `content` | TEXT | Full message content (no truncation) |
| `logged_by_luthien` | TEXT | Always `Y` for Luthien-logged data |
| `call_id` | TEXT | For drill-down to raw events |

**Limitations:**
- Only extracts the **last user message** from multi-message requests
- **Tool calls are not shown** - only text content is extracted
- `session_id` is only populated for certain event types (may be NULL)

**Example usage:**
```sql
-- View recent conversation
SELECT * FROM conversation_transcript
ORDER BY created_at DESC
LIMIT 20;

-- Export session as CSV
\copy (SELECT * FROM conversation_transcript WHERE session_id = 'xxx') TO 'session.csv' WITH CSV HEADER;

-- View specific call's back-and-forth
SELECT created_at, prompt_or_response, LEFT(content, 100) as preview
FROM conversation_transcript
WHERE call_id = 'xxx'
ORDER BY created_at;
```

## Querying Tips

### Export conversation to CSV
```bash
docker exec luthien-proxy-db-1 psql -U luthien -d luthien_control -c "\copy (SELECT * FROM conversation_transcript ORDER BY created_at DESC LIMIT 100) TO STDOUT WITH CSV HEADER" > conversation.csv
```

### Find calls by model
```sql
SELECT * FROM conversation_calls
WHERE model_name LIKE '%claude%'
ORDER BY created_at DESC;
```

### Debug a specific call
```sql
-- Get all events for a call
SELECT event_type, created_at, payload
FROM conversation_events
WHERE call_id = 'your-call-id'
ORDER BY created_at;
```
