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

