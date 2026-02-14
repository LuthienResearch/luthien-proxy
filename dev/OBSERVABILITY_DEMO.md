# Observability Demonstration Guide

**When to use this guide:** You're new to the observability system and want a hands-on walkthrough of all features.

This guide walks through testing the UppercaseNthWordPolicy and exploring all observability features.

**Other observability docs:**
- Need to view a specific trace? See [VIEWING_TRACES_GUIDE.md](VIEWING_TRACES_GUIDE.md)
- Understanding the architecture? Read [observability.md](observability.md)

## Prerequisites

1. **Set up environment** (if not already done):
   ```bash
   cp .env.example .env
   # Edit .env to add your OPENAI_API_KEY and/or ANTHROPIC_API_KEY
   ```

2. **Start the full observability stack** (includes gateway):
   ```bash
   ./scripts/observability.sh up -d
   ```
   This starts:
   - Gateway (with UppercaseNthWordPolicy)
   - PostgreSQL and Redis
   - Tempo (distributed tracing)

3. **Verify services are running**:
   ```bash
   docker compose --profile observability ps
   ```
   Should show: gateway, db, redis, tempo all running and healthy.

## Part 1: Verify Gateway

The gateway is now running in Docker with `UppercaseNthWordPolicy(n=3)` which will uppercase every 3rd word in responses.

```bash
# Check gateway logs
docker compose logs gateway --tail 20
```

You should see logs indicating:
- `Connected to database at postgresql://...`
- `Connected to Redis at redis://...`
- `Control plane initialized with OpenTelemetry tracing`
- `Application startup complete`

The gateway is available at `http://localhost:8000`.

## Part 2: Send Test Requests

### Option A: Using curl (Non-streaming)

```bash
curl -s "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-luthien-dev-key" \
  -d '{
    "model": "claude-opus-4-1",
    "messages": [{"role": "user", "content": "Say: the quick brown fox jumps over the lazy dog"}],
    "max_tokens": 50,
    "stream": false
  }' | jq -r '.choices[0].message.content'
```

**Expected**: Every 3rd word in the response should be UPPERCASE.

Example output:

```text
The quick BROWN fox jumps OVER the lazy DOG.
```

### Option B: Using curl (Streaming)

```bash
curl -N "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-luthien-dev-key" \
  -d '{
    "model": "claude-opus-4-1",
    "messages": [{"role": "user", "content": "Count from one to ten"}],
    "max_tokens": 50,
    "stream": true
  }'
```

Watch the words stream out with every 3rd word uppercased in real-time!

### Option C: Using Claude Code through the proxy

This is the most interesting option! Configure Claude Code to use the proxy:

1. **Find your Claude Code settings** (typically `~/.claude/config.json` or similar)

2. **Add proxy configuration**:
   ```json
   {
     "api": {
       "baseUrl": "http://localhost:8000/v1",
       "apiKey": "YOUR_API_KEY"
     }
   }
   ```

3. **Start a Claude Code session** and ask it something like:
   - "Explain how binary search works"
   - "Write a function to reverse a string"
   - "Tell me about quantum computing"

Watch as every 3rd word gets uppercased!

## Part 3: Real-Time Monitoring with Activity Monitor

While requests are flowing, open the **Activity Monitor**:

```bash
open http://localhost:8000/activity/monitor
```

### What You'll See:

1. **Real-time event stream** - Events appear as they happen:
   - `gateway.request_received` - Blue border
   - `gateway.request_sent` - Purple border
   - `gateway.response_received` - Green border
   - `gateway.response_sent` - Teal border
   - `policy.uppercase_*` - Orange border (policy events)

2. **Filter the events**:
   - **By Call ID**: Copy a call_id from an event, paste into filter → see all events for that request
   - **By Model**: Type "gpt-3.5" → see only GPT-3.5 requests
   - **By Event Type**: Select "Policy Events" → see only policy transformations

3. **Event Details** you can inspect:
   - Call ID (for correlation across tools)
   - Model being used
   - Request/response content (preview)
   - Policy event summaries (e.g., "Uppercased every 3th word in response")

### Try This:

1. Send several requests with different models
2. Use the model filter to show only specific models
3. Pick a call_id and copy it (we'll use it next!)

## Part 4: Deep Dive with Diff Viewer

Now let's see **exactly** what the policy changed. Open the **Diff Viewer**:

```bash
open http://localhost:8000/debug/diff
```

### Viewing a Specific Call:

1. **Paste the call_id** you copied from the activity monitor
2. Click "Load Diff"

### What You'll See:

**Request Diff (Left/Right columns)**:
- Original messages on the left
- Final messages on the right (should be identical - policy doesn't change requests)
- Metadata changes highlighted (if any)

**Response Diff (Left/Right columns)**:
- **Original Content**: The LLM's raw response
- **Final Content**: After policy transformation (every 3rd word UPPERCASED!)
- Changed text is highlighted in orange
- Word-by-word comparison

**Tempo Trace Link**:
- Click the "View Trace in Tempo" button
- Opens the Tempo API search for the full distributed trace

### Browse Recent Calls:

1. Click "Browse Recent" button
2. See the last 20 calls with timestamps
3. Click any call to load its diff

### JSON Highlighting:

If the response contains JSON (from a tool call or structured output), you'll see:
- Syntax highlighting (keys in blue, strings in green, numbers in orange)
- Pretty-printed formatting
- But the policy doesn't uppercase JSON - only text content!

## Part 5: Trace Correlation (The Full Picture)

Let's trace a single request through the entire system:

1. **Send a request** (note the response)

2. **Activity Monitor** → Find the request by model/time
   - Copy the call_id

3. **Diff Viewer** → Paste call_id
   - See the before/after transformation
   - Click "View Trace in Tempo"

4. **Tempo API** → Examine the trace:
   - `gateway.chat_completions` span (root)
     - `control_plane.process_request` span
       - `policy.process_request` span
     - `litellm.completion` span (LLM call)
     - `control_plane.process_response` span
       - `policy.process_full_response` span

5. **Back to Diff Viewer** → Verify the transformation matches what you saw

## Part 6: Testing Edge Cases

### Very Short Response

```bash
curl -s "http://localhost:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Say: one two three four five"}],
    "max_tokens": 20
  }' | jq
```

Expected: "one two THREE four FIVE" (or similar short response)

### Streaming with Word Boundaries

Send a streaming request and watch how the policy handles word boundaries across chunks.

### Multiple Requests in Parallel

Open multiple terminal windows and send requests simultaneously:

```bash
# Terminal 1
for i in {1..5}; do
  curl -s "http://localhost:8000/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer YOUR_API_KEY" \
    -d "{\"model\":\"gpt-3.5-turbo\",\"messages\":[{\"role\":\"user\",\"content\":\"Count to $i\"}],\"max_tokens\":20}" &
done
wait
```

Then check:
- Activity Monitor → See all 5 requests
- Diff Viewer → Browse recent calls to see all 5

## Part 7: Policy Events in Detail

Back to the **Activity Monitor**, filter to show only **Policy Events**:

1. Select "Policy Events" in the event type dropdown
2. Observe the policy event details:
   - `policy.uppercase_request` - "Request passed through (policy only affects responses, n=3)"
   - `policy.uppercase_applied` - "Uppercased every 3th word in response"
     - Details: original_preview, transformed_preview, word_count, n
   - `policy.uppercase_streaming_started` - "Started streaming transformation (n=3)"
   - `policy.uppercase_streaming_complete` - "Completed streaming transformation"
     - Details: chunks_processed, words_transformed, n

## Summary: What We've Demonstrated

- **Real-time Monitoring**: Activity Monitor showing live events with filtering
- **Policy Debugging**: Diff Viewer showing exact before/after changes
- **Distributed Tracing**: Tempo traces correlating requests across components
- **Event Emission**: Policy emitting structured events for observability

## Key Observability Features

1. **Call ID Correlation**: Same call_id across all systems (activity monitor, diff viewer, Grafana, Tempo, Loki)
2. **Non-blocking Persistence**: Events written to PostgreSQL via background queue (no request latency impact)
3. **Real-time Streaming**: SSE-based activity monitor updates instantly
4. **Structured Diffs**: API computes diffs server-side for easy visualization
5. **Policy Transparency**: Every policy transformation is visible and traceable

## Troubleshooting

**Activity Monitor not showing events?**
- Check Redis is running: `docker compose ps redis`
- Check gateway logs for Redis connection errors

**Diff Viewer returns 404?**
- Check PostgreSQL is running: `docker compose ps postgres`
- Verify events were written: `uv run python scripts/query_debug_logs.py --call-id <call_id>`

**Policy not transforming text?**
- Check gateway logs for policy initialization
- Verify UppercaseNthWordPolicy is configured in main.py
- Test with non-streaming first (easier to debug)

## Shutting Down

When you're done exploring:

```bash
./scripts/observability.sh down
```

This stops all services (gateway, databases, observability stack).

## Next Steps

- Try changing `n` to a different value in [main.py:78](../src/luthien_proxy/main.py#L78) (e.g., n=2 for every other word, n=1 for all words)
- Create your own policy by copying [UppercaseNthWordPolicy](../src/luthien_proxy/policies/uppercase_nth_word.py)
- Add more complex transformations (e.g., word filtering, content moderation)
- Export interesting traces and diffs for documentation
