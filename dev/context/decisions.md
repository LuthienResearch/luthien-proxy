# Technical Decisions

Why certain approaches were chosen over alternatives.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (decision + rationale).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## V2 Architecture: Integrated Gateway (2025-10-24)

**Decision**: Replace separate litellm-proxy + control-plane services with single integrated V2 gateway.

**Rationale**:
- **Simpler deployment**: One service instead of two, easier to reason about
- **Better performance**: No network hop between proxy and control plane
- **Cleaner code**: Direct function calls instead of HTTP callbacks
- **Easier testing**: Single process to start/stop, no inter-service coordination

**Trade-offs accepted**:
- Lost separation of concerns (but gained simplicity)
- Single process means single point of failure (but easier to monitor/restart)

## Event-Driven Policy DSL (2025-10-24)

**Decision**: Use lifecycle hooks (on_chunk_started, on_content_chunk, etc.) instead of callbacks.

**Rationale**:
- **Stream-aware**: Policies can buffer, transform, or block streaming responses
- **Cleaner interface**: Explicit hooks for different event types
- **Better composition**: Easier to layer policies or implement middleware patterns
- **Type safety**: Strongly typed parameters for each hook

**Example policies**: NoOpPolicy, UppercaseNthWord, ToolCallJudgeV3

## Configuration: V2_POLICY_CONFIG (2025-10-24)

**Decision**: Use `V2_POLICY_CONFIG` environment variable pointing to YAML file for policy configuration.

**Rationale**:
- Load policy class dynamically without code changes
- Support different policies per environment (dev/staging/prod)
- Simple YAML format: `policy.class` and `policy.config` sections

**Example**:
```yaml
policy:
  class: "luthien_proxy.v2.policies.tool_call_judge_v3:ToolCallJudgeV3Policy"
  config:
    model: "ollama/gemma2:2b"
    api_base: "http://local-llm:11434"
```

## Conversation Storage (2025-10-24)

**Decision**: Use `conversation_calls` and `conversation_events` tables for request/response persistence.

**Rationale**:
- **Structured storage**: SQL-queryable request/response pairs
- **Background queue**: Non-blocking persistence via `SequentialTaskQueue`
- **Complete payloads**: Store full OpenAI-format request/response, not streaming chunks
- **Streaming handled separately**: Chunks only for live monitoring (Redis) and debugging (debug_logs)

**Schema**:
- `conversation_calls`: call_id, model_name, status, timestamps
- `conversation_events`: call_id, event_type (request|response), sequence, payload (jsonb)

## OpenTelemetry for Observability (2025-10-24)

**Decision**: Use OpenTelemetry for distributed tracing and log correlation.

**Rationale**:
- **Industry standard**: Works with Grafana Tempo, Jaeger, etc.
- **Automatic instrumentation**: FastAPI + httpx already traced
- **Custom spans**: Add luthien-specific attributes (call_id, policy decisions, chunk counts)
- **Log correlation**: Inject trace_id/span_id into all log messages
- **Optional**: Can run V2 without observability stack (degrades gracefully)

**Stack**: Tempo (traces), Loki (logs), Promtail (collection), Grafana (visualization)

## Platform Vision (2025-10-24)

**Decision**: Build general-purpose infrastructure for LLM policy enforcement.

**Rationale**: Support both simple policies (rate limiting, content filtering) and complex adversarially robust policies (AI Control methodology).

The V2 architecture supports this range:
- Event-driven policies allow complex streaming transformations
- Policy context for per-request state management
- OpenTelemetry for deep observability of policy decisions
- Reference implementations from simple (NoOp) to complex (ToolCallJudge)

This is infrastructure-first: AI Control is an important use case, not the defining architecture.

---

(Add new decisions as they're made with timestamps: YYYY-MM-DD)
