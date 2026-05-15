git_sha: 342635d541115560f5b9af33031fa58f494ca753
browser_version: 1.50.0
backend: sqlite
generated_at: 2026-05-15T19:08:32.892039+00:00

# Luthien Admin UI — Performance Baseline Report

## Hardware & Versions

| Field | Value |
|-------|-------|
| Machine | x86_64 |
| Processor | i386 |
| RAM | 38 GB |
| OS | Darwin 22.6.0 |
| Python | 3.13.5 |
| git_sha | `342635d541115560f5b9af33031fa58f494ca753` |
| DB backend | sqlite |
| Playwright | 1.50.0 |

## Per-Page Timings

_NO DATA YET — run `scripts/run_perf.sh` to populate._

## Throttled (sami-like)

_NO DATA YET_

## Transcript Open

_NO DATA YET_

## SSE Memory Growth

_NO DATA YET_

## Server-Timing Breakdown

_NO DATA YET_

## Payload Size Breakdown

_NO DATA YET_

## Query Plans

---
git_sha: 342635d541115560f5b9af33031fa58f494ca753
timestamp: 2026-05-15T19:08:11.641641+00:00
backend: sqlite
row_count: 20528
session_count: 178
---

## Query: session_list

### SQL

```sql
SELECT
    ce.session_id,
    MIN(ce.created_at) as first_ts,
    MAX(ce.created_at) as last_ts,
    COUNT(*) as total_events,
    COUNT(DISTINCT ce.call_id) as turn_count,
    SUM(CASE
        WHEN ce.event_type LIKE 'policy.%'
        AND ce.event_type NOT LIKE 'policy.%judge.evaluation%'
        THEN 1 ELSE 0
    END) as policy_interventions
FROM conversation_events ce
WHERE ce.session_id IS NOT NULL
GROUP BY ce.session_id
ORDER BY last_ts DESC
LIMIT ? OFFSET ?
```

### EXPLAIN QUERY PLAN

```
SEARCH ce USING INDEX idx_conversation_events_session_id_btree (session_id>?)
USE TEMP B-TREE FOR count(DISTINCT)
USE TEMP B-TREE FOR ORDER BY
```

## Query: session_detail

### SQL

```sql
SELECT call_id, event_type, payload, created_at
FROM conversation_events
WHERE session_id = ?
ORDER BY created_at ASC
```

### EXPLAIN QUERY PLAN

```
SEARCH conversation_events USING INDEX idx_conversation_events_session_id_btree (session_id=?)
USE TEMP B-TREE FOR ORDER BY
```

## Query: recent_calls

### SQL

```sql
SELECT
    call_id,
    COUNT(*) as event_count,
    MAX(created_at) as latest,
    MAX(session_id) as session_id
FROM conversation_events
GROUP BY call_id
ORDER BY latest DESC
LIMIT ?
```

### EXPLAIN QUERY PLAN

```
SCAN conversation_events
USE TEMP B-TREE FOR GROUP BY
USE TEMP B-TREE FOR ORDER BY
```

## Top Hotspots

_NO DATA YET — hotspots will be derived from measurement results._

**Known candidates (from code review):**

1. `history_list.html:514` — hardcodes `?limit=10000` (sends full dataset on every load)
2. `conversation_live.js:92-118` — `loadInitial()` fetches entire session upfront
3. `conversation_live.js:215-244` — full DOM re-render on every SSE event
4. `conversation_live.js:164-172` — unbounded `rawEvents[callId]` array (memory leak risk)
5. `history_list.html:423-448` — client-side filter runs on every keystroke

**Query plan risks:**

- `session_list`: 2× TEMP B-TREE (COUNT DISTINCT + ORDER BY) — scales poorly with row count
- `recent_calls`: SCAN on all rows — O(n) over conversation_events

## Postgres

SKIPPED: Postgres not available in local dev environment.
