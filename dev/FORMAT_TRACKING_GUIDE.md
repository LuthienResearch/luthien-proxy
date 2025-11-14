# Format Tracking Guide

THIS IS A DRAFT, SAVING FOR WIP PURPOSES, BUT DO NOT TRUST THIS.
This guide explains how to track data format transformations through the Luthien proxy using our observability infrastructure.

## Overview

Every request through the proxy goes through multiple format transformations:

1. **Incoming request** - Raw request from client (Anthropic or OpenAI format)
2. **Format conversion** - If Anthropic endpoint, convert to internal OpenAI format
3. **Backend request** - Final request sent to LLM after policy processing
4. **Backend response** - Response from LLM
5. **Client response** - Final response sent to client

We track all these stages using structured logging with the `FORMAT_TRACKING` prefix.

## Viewing Format Tracking Logs

### Using Grafana/Loki (Recommended)

1. Open Grafana at http://localhost:3000
2. Go to Explore
3. Select Loki as the data source
4. Use LogQL queries to filter format tracking logs:

```logql
{service_name="gateway"} |= "FORMAT_TRACKING"
```

### Example Queries

**See all format transformations for a specific transaction:**
```logql
{service_name="gateway"} |= "FORMAT_TRACKING" |= "abc-123-transaction-id"
```

**See incoming requests only:**
```logql
{service_name="gateway"} |= "FORMAT_TRACKING: Incoming"
```

**See format conversions (anthropic→openai):**
```logql
{service_name="gateway"} |= "FORMAT_TRACKING: Conversion"
```

**See what was sent to backend:**
```logql
{service_name="gateway"} |= "FORMAT_TRACKING: Backend request"
```

**Filter by time range:**
Use Grafana's time picker in the top right to filter logs by time range.

### Using Docker Logs (Quick Check)

For quick debugging without Grafana:

```bash
# See recent format tracking logs
docker compose logs gateway | grep FORMAT_TRACKING

# Follow format tracking logs in real-time
docker compose logs -f gateway | grep FORMAT_TRACKING

# See format tracking for specific call ID
docker compose logs gateway | grep "abc-123-transaction-id" | grep FORMAT_TRACKING
```

## Understanding the Log Format

Each FORMAT_TRACKING log line includes:

- **Transaction ID**: Unique ID for the request/response cycle
- **Stage**: What transformation stage (Incoming/Conversion/Backend request)
- **Message count**: Number of messages in the conversation
- **Message details**: Role and content preview for each message

Example:
```
[abc-123] FORMAT_TRACKING: Incoming anthropic request to /v1/messages, messages=3
[abc-123] FORMAT_TRACKING: Conversion anthropic_to_openai, result_messages=4
[abc-123] FORMAT_TRACKING:   Message 0: role=user, content=write helloworld.py
[abc-123] FORMAT_TRACKING:   Message 1: role=assistant, content=I'll create...
[abc-123] FORMAT_TRACKING:   Message 2: role=tool, content=The user doesn't want...
[abc-123] FORMAT_TRACKING:   Message 3: role=user, content=Actually make it 'helloborld.py'
```

## Debugging Format Conversion Issues

When debugging format conversion issues (like the tool rejection bug):

1. Find the transaction ID from the client or gateway logs
2. Query Loki for all FORMAT_TRACKING logs for that transaction
3. Compare the message structure at each stage:
   - **Incoming**: What did the client send?
   - **Conversion**: How did we transform it?
   - **Backend**: What did we send to the LLM?
4. Look for missing messages or incorrect transformations

## Event Types in Observability

The format tracking also emits structured events to:
- **OpenTelemetry spans**: See in Tempo (http://localhost:3011)
- **Redis pub/sub**: For real-time monitoring
- **Logs**: Structured JSON logs in Loki

Event types:
- `luthien.request.incoming` - Raw incoming request
- `luthien.request.format_conversion` - Format conversion result
- `luthien.backend.request` - Request sent to backend
- `luthien.backend.response` - Response from backend
- `luthien.response.outgoing` - Response sent to client

## Example: Debugging Tool Rejection Issue

The tool rejection bug was debugged by:

1. Making a request where a tool call was rejected
2. Finding the transaction ID from logs
3. Querying Loki: `{service_name="gateway"} |= "FORMAT_TRACKING" |= "transaction-id"`
4. Observing that after conversion, we had:
   - Message 0: role=user (original request)
   - Message 1: role=assistant (with tool_use)
   - Message 2: role=tool (rejection)
   - **Missing**: Message 3: role=user (the actual new request!)
5. This showed the conversion was dropping the user's follow-up message
6. Fixed by updating `anthropic_to_openai_request` to handle mixed tool_result + text content

## Tips

- Use the transaction ID to track a request through the entire pipeline
- The `FORMAT_TRACKING` logs show the actual message structure at each stage
- Compare incoming vs backend request to see what transformations occurred
- Look for message count mismatches (indicates dropped messages)
