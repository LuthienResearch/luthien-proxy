# User Story 5: Infrastructure - Observability and Pipeline Unification

## Persona

**Platform/Core Developer** - Developer working on Luthien Control internals.

## Context

The current gateway has duplicated code between OpenAI and Anthropic endpoints, and lacks structured span hierarchy for observability. This makes debugging difficult and creates maintenance burden.

## Story

> As a core developer, I want a unified request processing pipeline with structured observability spans, so that I can debug issues effectively and maintain the codebase efficiently.

## Current State Analysis

### Duplicated Code in `gateway_routes.py`

The `/v1/chat/completions` and `/v1/messages` endpoints share ~80% of their logic:

```
Both endpoints:
├── Check request size
├── Parse request body
├── Generate call_id
├── Set span attributes
├── Log client request
├── Create PolicyContext
├── Create pipeline dependencies (recorder, executor, formatter)
├── Create PolicyOrchestrator
├── Process request through policy
├── Log backend request
├── Branch: streaming vs non-streaming
│   ├── Streaming: llm_client.stream() → orchestrator.process_streaming_response()
│   └── Non-streaming: llm_client.complete() → orchestrator.process_full_response()
└── Return response with headers
```

**Differences (only at boundaries):**
- Anthropic: `anthropic_to_openai_request()` on ingress
- Anthropic: `openai_to_anthropic_response()` on egress (non-streaming)
- Different `ClientFormatter` instance

### Current Span Structure

```
HTTP POST /v1/chat/completions (FastAPI auto-instrumented)
└── (all processing happens in this single span)
```

No visibility into pipeline phases.

## Target Architecture

### Unified Pipeline

```python
# gateway_routes.py - thin endpoint handlers
@router.post("/v1/chat/completions")
async def chat_completions(request: Request, ...):
    return await process_llm_request(
        request=request,
        client_format=ClientFormat.OPENAI,
        ...
    )

@router.post("/v1/messages")
async def anthropic_messages(request: Request, ...):
    return await process_llm_request(
        request=request,
        client_format=ClientFormat.ANTHROPIC,
        ...
    )
```

```python
# pipeline/processor.py - unified processing logic
async def process_llm_request(
    request: Request,
    client_format: ClientFormat,
    policy: PolicyProtocol,
    llm_client: LLMClient,
    emitter: EventEmitterProtocol,
) -> Response:
    with tracer.start_as_current_span("transaction_processing") as root_span:
        # Phase 1: Process incoming request
        with tracer.start_as_current_span("process_request"):
            body = await ingest_request(request, client_format)
            request_message = await policy_process_request(body, policy, ctx)

        # Phase 2: Send to upstream LLM
        with tracer.start_as_current_span("send_upstream"):
            if is_streaming:
                backend_stream = await llm_client.stream(request_message)
            else:
                backend_response = await llm_client.complete(request_message)

        # Phase 3: Process response (streaming or full)
        with tracer.start_as_current_span("process_response"):
            processed = await policy_process_response(...)

        # Phase 4: Send to client
        with tracer.start_as_current_span("send_to_client"):
            return format_response(processed, client_format)
```

### Target Span Hierarchy

```
transaction_processing (root)
├── process_request
│   ├── format_conversion (if Anthropic)
│   ├── policy.on_request_received
│   └── policy.on_request_processed
├── send_upstream
│   └── litellm.acompletion / litellm.acompletion_stream
├── process_response
│   ├── policy.on_chunk_started (streaming, repeated)
│   ├── policy.on_content_chunk (streaming, repeated)
│   ├── policy.on_tool_call_chunk (streaming, repeated)
│   ├── policy.on_response_completed
│   └── policy.custom_spans (arbitrary nesting)
└── send_to_client
    ├── format_conversion (if Anthropic)
    └── sse_formatting (if streaming)
```

### Key Design Principles

1. **Sibling Spans**: Pipeline phases are siblings under root, not nested
2. **Policy Spans**: Policies can create arbitrary subspans under `process_response`
3. **Format Agnostic Core**: All processing uses OpenAI format internally
4. **Boundary Conversion**: Format conversion only at ingress/egress
5. **Span Events**: Key events logged as span events, not separate spans

## Acceptance Criteria

- [ ] Single `process_llm_request()` function handles both endpoints
- [ ] Endpoint handlers are <10 lines each (just delegation)
- [ ] Root `transaction_processing` span wraps all processing
- [ ] Four sibling child spans: `process_request`, `send_upstream`, `process_response`, `send_to_client`
- [ ] Policy hooks can create arbitrary nested spans
- [ ] Span attributes include: `call_id`, `model`, `stream`, `client_format`
- [ ] Span events log key transitions without creating span overhead
- [ ] Grafana/Tempo can visualize the pipeline phases clearly

## Required Features

### Core Infrastructure

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-en1` | Unify OpenAI and Anthropic endpoint processing | open | P1 |
| `luthien-proxy-a0r` | Structured span hierarchy for request processing | open | P1 |

### Related Existing Issues

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-u78` | Improve error handling for OpenTelemetry spans | open | P2 |
| `luthien-proxy-h3s` | Review observability stack | open | P3 |

## Implementation Status

**Overall Progress**: Not Started

### Phase 1: Extract Unified Pipeline
- [ ] Create `pipeline/processor.py` with `process_llm_request()`
- [ ] Define `ClientFormat` enum (OPENAI, ANTHROPIC)
- [ ] Move shared logic to processor
- [ ] Keep format converters at boundaries
- [ ] Update endpoint handlers to delegate

### Phase 2: Add Span Hierarchy
- [ ] Create root `transaction_processing` span
- [ ] Add `process_request` span
- [ ] Add `send_upstream` span
- [ ] Add `process_response` span
- [ ] Add `send_to_client` span
- [ ] Ensure sibling (not nested) structure

### Phase 3: Policy Span Support
- [ ] Pass span context to PolicyContext
- [ ] Add `create_span()` helper to PolicyContext
- [ ] Document span creation for policy authors
- [ ] Test nested policy spans in Grafana

### Phase 4: Span Events
- [ ] Add span events for format conversions
- [ ] Add span events for policy decisions
- [ ] Add span events for streaming milestones
- [ ] Reduce span overhead vs. current logging

## Technical Touchpoints

- `gateway_routes.py`: Thin endpoint handlers
- `pipeline/processor.py`: New unified processing logic
- `pipeline/spans.py`: Span hierarchy management
- `policy_core/policy_context.py`: Span context access for policies
- `observability/`: Span event helpers

## File Structure (Target)

```
src/luthien_proxy/
├── gateway_routes.py          # Thin handlers (~30 lines total)
├── pipeline/
│   ├── __init__.py
│   ├── processor.py           # process_llm_request()
│   ├── spans.py               # Span hierarchy helpers
│   └── format_conversion.py   # Ingress/egress converters
├── llm/
│   └── client.py              # (unchanged)
├── orchestration/
│   └── policy_orchestrator.py # (unchanged, called from processor)
└── ...
```

## Span Attribute Standards

```python
# Root span attributes
span.set_attribute("luthien.transaction_id", call_id)
span.set_attribute("luthien.client_format", "openai" | "anthropic")
span.set_attribute("luthien.model", model_name)
span.set_attribute("luthien.stream", is_streaming)

# Phase span attributes
span.set_attribute("luthien.phase", "process_request" | "send_upstream" | ...)
span.set_attribute("luthien.duration_ms", duration)

# Policy span attributes (when policies create spans)
span.set_attribute("luthien.policy", policy_class_name)
span.set_attribute("luthien.policy_phase", "request" | "response" | "tool_call")
```

## Notes

- Streaming responses need careful span management (span must stay open during stream)
- Consider using `span.add_event()` for high-frequency events vs. creating subspans
- Policy-created spans should be children of `process_response`, not siblings
- Error handling should record exceptions on the appropriate span
- Consider span sampling for high-volume deployments
