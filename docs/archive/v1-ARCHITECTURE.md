# Luthien Architecture

## Executive Summary

Luthien intercepts LLM requests/responses via LiteLLM callbacks and forwards them to a control plane that executes **policies** (Python classes). Policies can inspect, transform, block, or augment traffic. Both original and policy-modified versions are logged to Postgres and published to Redis for monitoring.

## Architecture

```
Client → LiteLLM Proxy → Callback → Control Plane → Policy
                ↓                        ↓
            Backend (OpenAI, etc)    Postgres + Redis
```

**Components:**
- **LiteLLM proxy** (`config/litellm_config.yaml`) - Routes requests to backend providers
- **Unified callback** (`config/unified_callback.py`) - Thin forwarding layer, normalizes provider formats
- **Control plane** (FastAPI) - Executes policies, logs events, publishes to Redis
- **Policies** (`src/luthien_proxy/policies/`) - Python classes implementing control logic

## Component Details

### LiteLLM Proxy
- Config: `config/litellm_config.yaml`
- Schema: `prisma/litellm/schema.prisma`
- Bootstrap: `src/luthien_proxy/proxy/__main__.py:9-56` (runs migrations before start)
- Callback: Configured as `callbacks: ["unified_callback.unified_callback"]` (line 80)

### Control Plane
- App init: `src/luthien_proxy/control_plane/app.py:107-166`
- Lifespan: Creates `app.state.db_pool` and `app.state.redis_client`
- Policy loading: From `LUTHIEN_POLICY_CONFIG` (default `config/luthien_config.yaml`)
- Schema: `prisma/control_plane/schema.prisma` (deployed via `prisma migrate deploy`)

### Hook Routes
- Generic handler: `src/luthien_proxy/control_plane/hooks_routes.py:80-173`
- Flow: log → policy → log → DB → Redis → return
- Parameter filtering: Inspects policy method signature, only passes matching params

### Streaming Routes
- WebSocket handler: `src/luthien_proxy/control_plane/streaming_routes.py:385` (policy_stream_endpoint)
- Event publisher: `_StreamEventPublisher` logs to `debug_logs` and publishes to Redis
- Orchestrator: `StreamOrchestrator` (`src/luthien_proxy/proxy/stream_orchestrator.py`) manages timeout and bidirectional flow

## Data Storage

**Postgres tables:**
1. `debug_logs` - Catch-all hook payloads (legacy)
2. `conversation_calls` - Call metadata (model, status, timing)
3. `conversation_events` - Structured request/response events with sequence ordering
4. `policy_events` - Optional policy decision log (explicit calls only)

**Redis usage:**
1. **Pub/sub** - Live event stream on `luthien:conversation:{call_id}`
2. **Stream context** - Accumulate text in `stream:{call_id}:text` (TTL 1h)
3. **LiteLLM cache** - Response caching (managed by LiteLLM)

**Event format example** (pub/sub):
```json
{
  "call_id": "abc-123",
  "event_type": "response",
  "sequence": 1,
  "payload": {
    "original": {"message": {"content": "Paris"}},
    "final": {"message": {"content": "[REDACTED]"}},
    "message": {"content": "[REDACTED]"}
  }
}
```

Listeners can compare `payload.original` vs `payload.final` within single event.

**Schema details:** See `prisma/control_plane/schema.prisma` and `prisma/litellm/schema.prisma`

## Architectural Decisions

**Why WebSocket for streaming?**
- Bidirectional (control plane can send KEEPALIVE during long processing)
- Low latency, stateful connection matches streaming semantics
- Built-in FastAPI support

**Why normalize Anthropic to OpenAI?**
- Policies are provider-agnostic
- No provider-specific logic in control plane
- Easier to add new providers (normalization in callback only)

**Why two logging systems?**
- `debug_logs`: Legacy catch-all, easy to add without schema changes
- `conversation_events`: Structured, efficient queries, cascade deletes
- Keep both for now - `conversation_events` preferred for new code

**Why optional policy_events?**
- Not all policies make "decisions" worth logging separately
- Passthrough policies (NoOp, AllCaps) don't need decision log
- Control policies (LLMJudge, SQLProtection) benefit from structured records
- Design: Opt-in via explicit `record_policy_event()` calls

## Data Retention

**Current state**: All tables append-only, no automatic cleanup.

**Manual cleanup:**
```sql
-- Delete old debug logs (>7 days)
DELETE FROM debug_logs WHERE time_created < NOW() - INTERVAL '7 days';

-- Delete old calls and cascade events (>30 days)
DELETE FROM conversation_calls WHERE created_at < NOW() - INTERVAL '30 days';
```

**Redis TTLs:**
- Stream context: 1 hour (auto-expire)
- Pub/sub: ephemeral (no retention)
- LiteLLM cache: 5 minutes

**TODO**: Implement retention policy in control plane lifespan (see `dev/TODO.md`).

## Cross-References

**For implementation details:**
- Policy examples: `src/luthien_proxy/policies/`
- Database schemas: `prisma/control_plane/schema.prisma`, `prisma/litellm/schema.prisma`
- Observability docs: [observability.md](observability.md) (if exists)
- Visual flows: [diagrams.md](diagrams.md)
- Learning path: [developer-onboarding.md](developer-onboarding.md)
