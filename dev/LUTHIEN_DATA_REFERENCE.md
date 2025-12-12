# Luthien Data Reference - What Gets Logged

**Created:** 2025-12-12
**Purpose:** Quick reference for querying Luthien's captured data

---

## Database Tables

### 1. `conversation_events` - Main event stream

**Captures:**
- Every step in the request/response pipeline
- User messages, assistant responses
- Policy decisions, format conversions
- Full payloads in JSONB

**Schema:**
```sql
id          uuid
call_id     text                -- Groups events for one request
event_type  text                -- e.g., "pipeline.client_request"
payload     jsonb               -- Full event data
created_at  timestamp
```

**Event types captured:**
- `pipeline.client_request` - Incoming request from Claude Code
- `pipeline.format_conversion` - OpenAI ↔ Anthropic conversion
- `transaction.request_recorded` - Final request sent to LLM
- `pipeline.backend_request` - Request sent to upstream API
- `transaction.non_streaming_response_recorded` - Full response
- `transaction.streaming_response_recorded` - Streaming response
- `pipeline.client_response` - Response sent back to client

### 2. `conversation_calls` - Call-level metadata

**Captures:**
- High-level info about each request
- Model used, provider, status, timing

**Schema:**
```sql
call_id      text
model_name   text
provider     text
status       text
created_at   timestamp
completed_at timestamp
```

### 3. `policy_events` - Policy-specific actions

**Captures:**
- When policies modify/block requests
- Policy configuration used
- Original vs modified events

**Schema:**
```sql
id                uuid
call_id           text
policy_class      text       -- e.g., "ToolCallJudgePolicy"
event_type        text       -- e.g., "blocked", "modified"
original_event_id uuid       -- Links to conversation_events
modified_event_id uuid
metadata          jsonb      -- Policy-specific data
created_at        timestamp
```

### 4. `debug_logs` - Debug information

**Captures:**
- Internal debugging data
- Error traces
- Performance metrics

---

## Useful Queries

### Recent Activity
```sql
-- Last 10 requests
SELECT
  created_at,
  call_id,
  event_type,
  payload->>'model' as model
FROM conversation_events
WHERE event_type = 'pipeline.client_request'
ORDER BY created_at DESC
LIMIT 10;
```

### Count Events by Type
```sql
SELECT
  event_type,
  COUNT(*) as count
FROM conversation_events
GROUP BY event_type
ORDER BY count DESC;
```

### Full Conversation Flow
```sql
-- See all events for a specific call_id
SELECT
  created_at,
  event_type,
  jsonb_pretty(payload) as payload
FROM conversation_events
WHERE call_id = '<call_id>'
ORDER BY created_at;
```

### User Messages Over Time
```sql
-- Extract user messages
SELECT
  created_at,
  call_id,
  payload->'payload'->'messages'->0->>'content' as user_message
FROM conversation_events
WHERE event_type = 'pipeline.client_request'
ORDER BY created_at DESC
LIMIT 20;
```

### Policy Actions
```sql
-- See what policies did
SELECT
  created_at,
  policy_class,
  event_type,
  jsonb_pretty(metadata) as details
FROM policy_events
ORDER BY created_at DESC;
```

---

## How to Query

### From Command Line
```bash
# List tables
docker exec luthien-proxy-db-1 psql -U luthien -d luthien_control -c "\dt"

# Run custom query
docker exec luthien-proxy-db-1 psql -U luthien -d luthien_control -c "
SELECT * FROM conversation_events ORDER BY created_at DESC LIMIT 5;
"
```

### From Python (in a policy)
```python
import asyncpg

async def query_recent_sessions(db_url: str, limit: int = 10):
    """Get recent conversation sessions."""
    conn = await asyncpg.connect(db_url)

    rows = await conn.fetch("""
        SELECT DISTINCT call_id, created_at
        FROM conversation_events
        ORDER BY created_at DESC
        LIMIT $1
    """, limit)

    await conn.close()
    return rows
```

---

## What You Can Analyze

### For Commit Health Monitor Policy
```sql
-- Would need to capture git state in metadata
-- Example: Track file changes per session
SELECT
  call_id,
  COUNT(*) FILTER (WHERE event_type LIKE '%Write%') as writes,
  COUNT(*) FILTER (WHERE event_type LIKE '%commit%') as commits
FROM conversation_events
GROUP BY call_id;
```

### For Scope Creep Detector Policy
```sql
-- Compare user request vs actual tool calls
SELECT
  e1.call_id,
  e1.payload->'payload'->'messages'->0->>'content' as user_request,
  COUNT(e2.*) as tool_calls_made
FROM conversation_events e1
LEFT JOIN conversation_events e2 ON e1.call_id = e2.call_id AND e2.event_type LIKE '%tool%'
WHERE e1.event_type = 'pipeline.client_request'
GROUP BY e1.call_id, user_request;
```

### For Session Pattern Analyzer
```sql
-- Patterns across multiple sessions
WITH session_stats AS (
  SELECT
    call_id,
    created_at::date as date,
    COUNT(*) as events,
    COUNT(*) FILTER (WHERE event_type = 'pipeline.client_request') as requests
  FROM conversation_events
  GROUP BY call_id, created_at::date
)
SELECT
  date,
  COUNT(*) as sessions,
  AVG(requests) as avg_requests_per_session
FROM session_stats
GROUP BY date
ORDER BY date DESC;
```

---

## Current Data Stats

As of last query:
- **Total events:** 56 requests captured
- **Event types:** 7 different pipeline stages tracked
- **Recent calls:** 5 test requests from gateway testing

---

## Next Steps for Retro Analysis

**Manual approach:**
1. Query conversation_events for your work sessions
2. Look for patterns (file changes, time between commits, etc.)
3. Document learnings in retro

**Automated approach (future policy):**
1. Build "Retro Generator Policy" that queries this data
2. Analyzes patterns automatically
3. Generates insights in real-time or at session end

---

**Remember:** This data is what makes Luthien powerful for cross-session analysis - something Claude Code hooks can't do!
