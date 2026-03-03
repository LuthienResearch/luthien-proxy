# How to View OpenTelemetry Traces

**When to use this guide:** You have a specific `call_id` or trace you want to inspect via Tempo.

**Other observability docs:**
- Understanding the system? Read [observability.md](observability.md)

---

## Prerequisites

Make sure the observability stack is running:

```bash
# Check observability stack (Tempo)
./scripts/observability.sh status

# Check main application
docker compose ps
```

All services should show "Up" status.

---

## Step 1: Make Test Requests

Generate some trace data by making requests to the **gateway** at port 8000:

```bash
# Simple non-streaming request
curl -s "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-luthien-dev-key" \
  -d '{
    "model": "claude-opus-4-1",
    "messages": [{"role": "user", "content": "Say hello"}],
    "max_tokens": 20,
    "stream": false
  }' | jq '.choices[0].message.content'
```

---

## Step 2: Query Traces via Tempo HTTP API

```bash
# Search recent traces
curl http://localhost:3200/api/search | jq

# Search by call_id (TraceQL)
curl 'http://localhost:3200/api/search?q=%7B%20span.%22luthien.call_id%22%20%3D%20%22your-call-id%22%20%7D' | jq

# Search by policy name
curl 'http://localhost:3200/api/search?q=%7B%20span.%22luthien.policy.name%22%20%3D%20%22NoOpPolicy%22%20%7D' | jq

# Search for error traces
curl 'http://localhost:3200/api/search?q=%7B%20status%20%3D%20error%20%7D' | jq
```

### What You'll See in a Trace:

```
gateway.chat_completions (root span)
+-- control_plane.process_request
|   +-- policy events (if any)
+-- control_plane.process_streaming_response (if streaming)
    +-- orchestrator.start
    +-- orchestrator.complete
    +-- policy events
```

**Span Attributes:**
- `luthien.call_id` - Request identifier
- `luthien.model` - Model name
- `luthien.stream` - Is streaming
- `luthien.policy.name` - Policy used
- `orchestrator.chunk_count` - Number of chunks (streaming)

---

## Step 3: Use the Diff Viewer

The diff viewer at `http://localhost:8000/diffs` links directly to Tempo traces for each call. Paste a `call_id` to see the before/after policy transformation and click the trace link.

---

## Step 4: Real-Time Activity Monitor

```bash
open http://localhost:8000/activity/monitor
```

This shows live events from Redis pub/sub (separate from traces).

---

## Troubleshooting

### "No traces found"

**Check 1:** Are you making requests to the gateway at **port 8000**?

```bash
curl -s "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-luthien-dev-key" \
  -d '{
    "model": "claude-opus-4-1",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 20,
    "stream": false
  }'
```

**Check 2:** Is Tempo receiving data?

```bash
docker compose logs tempo --tail 50 | grep -i "spans"
```

**Check 3:** Can the gateway reach Tempo?

```bash
docker compose exec gateway curl -v http://tempo:4317
```

---

## Example TraceQL Queries

Attribute names containing dots must be quoted:

```traceql
{ span."luthien.call_id" = "abc123" }
{ span."luthien.policy.name" = "SQLProtectionPolicy" }
{ span."luthien.stream" = true }
{ status = error }
{ duration > 1s }
```

---

**For more details, see:**
- [dev/context/otel-conventions.md](./context/otel-conventions.md) - Naming conventions
