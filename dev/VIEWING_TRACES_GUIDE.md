# How to View OpenTelemetry Traces and Logs

**When to use this guide:** You have a specific `call_id` or trace you want to inspect in Grafana/Tempo.

**Quick Guide:** See traces and logs from proxy activity in Grafana

**Other observability docs:**
- New to observability? Start with [OBSERVABILITY_DEMO.md](OBSERVABILITY_DEMO.md)
- Understanding the system? Read [observability.md](observability.md)

---

## Prerequisites

Make sure both stacks are running:

```bash
# Check observability stack (Tempo, Loki, Grafana)
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

# Streaming request
curl -s "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-luthien-dev-key" \
  -d '{
    "model": "claude-opus-4-1",
    "messages": [{"role": "user", "content": "Count to 5"}],
    "max_tokens": 30,
    "stream": true
  }'
```

**Note:** The gateway (port 8000) provides an integrated LiteLLM proxy with OpenTelemetry instrumentation built-in.

---

## Step 2: Open Grafana

```bash
open http://localhost:3000
```

**Login credentials:**
- Username: `admin`
- Password: `admin`

(You may be prompted to change the password - you can skip this)

---

## Step 3: View Traces in Tempo

### Option A: Use the Pre-built Dashboard

1. Click **Dashboards** (📊 icon) in the left sidebar
2. Click **Import dashboard** (blue button)
3. Click **Upload JSON file**
4. Select: `observability/grafana-dashboards/luthien-traces.json`
5. Click **Load**
6. You should see:
   - Recent Traces (last 20)
   - Search by call_id
   - Search by policy
   - Error traces
   - Correlated logs

### Option B: Use Explore (Manual)

1. Click **Explore** (🧭 icon) in the left sidebar
2. Select **Tempo** from the datasource dropdown (top)
3. Click **Search** tab
4. You should see recent traces listed
5. Click on any trace to see the span details

### What You'll See in a Trace:

```
gateway.chat_completions (root span)
├── control_plane.process_request
│   └── policy events (if any)
└── control_plane.process_streaming_response (if streaming)
    ├── orchestrator.start
    ├── orchestrator.complete
    └── policy events
```

**Span Attributes:**
- `luthien.call_id` - Request identifier
- `luthien.model` - Model name
- `luthien.stream` - Is streaming
- `luthien.policy.name` - Policy used
- `orchestrator.chunk_count` - Number of chunks (streaming)

---

## Step 4: View Logs in Loki

### Option A: From a Trace (Trace → Logs Correlation)

1. In the trace view, look for any span
2. Click the span to see details
3. Look for **Logs for this span** button
4. Click it to jump to correlated logs

### Option B: Explore Logs Directly

1. Click **Explore** in the left sidebar
2. Select **Loki** from the datasource dropdown
3. In the query builder, enter:
   ```
   {service_name="luthien-proxy"}
   ```
4. Click **Run query** (or press Shift+Enter)
5. You should see logs with trace_id and span_id

### Log Format:

```
2024-01-15 10:30:00 INFO [trace_id=4bf92f3577b34da6 span_id=00f067aa0ba902b7] Processing request
```

**Filter logs:**
- By level: `{service_name="luthien-proxy"} |= "ERROR"`
- By call_id: `{service_name="luthien-proxy"} |= "call_id=abc123"`
- By policy: `{service_name="luthien-proxy"} |= "NoOpPolicy"`

---

## Step 5: Search for Specific Traces

### Search by Call ID

If you have a specific `call_id` from your application:

1. Go to **Explore** → **Tempo**
2. Click **Search** tab
3. Click **+ Add filter**
4. Select: `luthien.call_id` = `<your-call-id>`
5. Click **Run query**

### Search by Time Range

1. In Explore, use the time picker (top right)
2. Select "Last 15 minutes" or custom range
3. Run the search

### Search by Policy Name

1. **Explore** → **Tempo** → **Search**
2. Filter: `luthien.policy.name` = `NoOpPolicy`

### Search by Error Status

1. **Explore** → **Tempo** → **Search**
2. Filter: `status` = `error`
3. Shows only traces with errors

---

## Step 6: Real-Time Activity Monitor

For activity:

```bash
# Access the real-time monitor
open http://localhost:8000/activity/monitor
```

This shows live events from Redis pub/sub (separate from traces).

---

## Troubleshooting

### "No traces found"

**Check 1:** Are you making requests to the gateway?

The gateway with OpenTelemetry is at **port 8000**:
- `POST /v1/chat/completions` (OpenAI format)
- `POST /v1/messages` (Anthropic format)

```bash
# Test gateway endpoint (CORRECT - will create traces)
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

**Check 2:** Is OTEL_ENABLED set?

OpenTelemetry is always enabled for the gateway. No configuration needed!

**Check 3:** Is Tempo receiving data?

```bash
# Check Tempo logs
docker compose logs tempo --tail 50 | grep -i "spans"
```

**Check 4:** Can the gateway reach Tempo?

```bash
docker compose exec gateway curl -v http://tempo:4317
# Should connect (even if it returns an error, connection works)
```

### "Datasource not found"

The datasources should auto-configure. If not:

1. Go to **Configuration** → **Data sources**
2. Check that **Tempo** and **Loki** are listed
3. If missing, add them:
   - **Tempo:** URL = `http://tempo:3200`
   - **Loki:** URL = `http://loki:3100`

### "Logs don't show trace_id"

Check that telemetry is initialized:

```bash
docker compose logs gateway | grep -i "opentelemetry initialized"
# Should see: "OpenTelemetry initialized"
```

---

## Example Queries

### TraceQL (Tempo)

Find traces by attributes:

```traceql
{luthien.call_id="abc123"}
{luthien.policy.name="SQLProtectionPolicy"}
{luthien.stream=true}
{status=error}
{duration > 1s}
```

### LogQL (Loki)

Filter logs:

```logql
{service_name="luthien-proxy"} |= "ERROR"
{service_name="luthien-proxy"} |= "call_id=abc123"
{service_name="luthien-proxy"} | json | level="error"
{service_name="luthien-proxy"} | json | duration_ms > 1000
```

---

## Quick Demo Workflow

**End-to-end demo:**

```bash
# 1. Make a request and capture call_id
RESPONSE=$(curl -s "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-luthien-dev-key" \
  -d '{
    "model": "claude-opus-4-1",
    "messages": [{"role": "user", "content": "Test"}],
    "stream": false
  }')

# 2. Extract the response
echo "$RESPONSE" | jq '.'

# 3. Check gateway logs for trace_id
docker compose logs gateway --tail 20 | grep -i trace_id

# 4. Open Grafana and search for traces in last 5 minutes
open "http://localhost:3000/explore?orgId=1&left=%7B%22datasource%22%3A%22tempo%22%7D"
```

Then in Grafana:
1. Select **Tempo** datasource
2. Click **Search**
3. Set time range to "Last 5 minutes"
4. You should see your trace!

---

## Next Steps

1. **Import the dashboard:**
   - In Grafana: Dashboards → Import → Upload `observability/grafana-dashboards/luthien-traces.json`

2. **Create custom panels:**
   - Add metrics for request rate, latency percentiles, error rate

3. **Set up alerts:**
   - Alert on error traces
   - Alert on high latency (duration > threshold)

4. **Explore trace details:**
   - Click on spans to see attributes
   - View span events (policy.content_filtered, etc.)
   - Check span timing and duration

---

**For more details, see:**
- [dev/context/observability-guide.md](./context/observability-guide.md) - Full usage guide
- [dev/context/otel-conventions.md](./context/otel-conventions.md) - Naming conventions
