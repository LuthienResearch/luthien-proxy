# Architecture

This document describes how luthien-proxy is structured and how requests flow through it. It should take about 10 minutes to read.

## What Is Luthien

Luthien is an LLM gateway that sits between AI clients and backend LLM providers. It intercepts requests and responses, applying configurable **policies** that can observe, modify, or block LLM interactions. Think of it as a programmable HTTP proxy specifically designed for AI control.

```
Client (Claude Code, etc.)
    |
    v
Luthien Gateway (FastAPI)
    |-- Policy: inspect/modify request
    |-- Forward to backend LLM
    |-- Policy: inspect/modify response
    |
    v
Client receives (possibly modified) response
```

The gateway supports two API formats natively:
- **OpenAI** (`/v1/chat/completions`) — via LiteLLM, supporting any LiteLLM-compatible provider
- **Anthropic** (`/v1/messages`) — native Anthropic SDK, preserving features like extended thinking and prompt caching

## Request Lifecycle

A request flows through four phases. The entry points are `process_llm_request()` (OpenAI path) and `process_anthropic_request()` (Anthropic path) in `src/luthien_proxy/pipeline/`.

### 1. Ingest & Authenticate

`gateway_routes.py` receives the HTTP request, verifies the API key (proxy key, passthrough, or both modes via `CredentialManager`), and dispatches to the appropriate pipeline processor.

### 2. Policy on Request

A `PolicyContext` is created for this request (unique `transaction_id`, session tracking, observability emitter). The policy's request hook runs, potentially modifying the request before it reaches the backend.

### 3. Send to Backend

The (possibly modified) request is forwarded to the backend LLM. For OpenAI format, this goes through `LiteLLMClient`. For Anthropic format, through `AnthropicClient`.

### 4. Policy on Response & Send to Client

**Non-streaming:** The complete response passes through the policy's response hook, then is returned as JSON.

**Streaming (OpenAI path):** A three-stage async pipeline connected by `asyncio.Queue`s:

```
Backend stream (ModelResponse chunks)
    |
    v
PolicyExecutor: assembles chunks into blocks, fires policy hooks
    |  (on_content_complete, on_tool_call_complete, etc.)
    v
ClientFormatter: converts ModelResponse -> SSE strings
    |
    v
Client receives SSE events
```

**Streaming (Anthropic path):** The policy drives execution directly via `run_anthropic()`, yielding `MessageStreamEvent`s that are formatted as SSE and sent to the client.

## Module Map

### Core Pipeline

| Module | Responsibility |
|--------|---------------|
| `main.py` | App factory, lifespan management, dependency wiring |
| `gateway_routes.py` | HTTP endpoints, authentication |
| `pipeline/processor.py` | OpenAI request pipeline (phases 1-4) |
| `pipeline/anthropic_processor.py` | Anthropic request pipeline (phases 1-4) |
| `dependencies.py` | DI container (`Dependencies` dataclass), FastAPI `Depends()` functions |

### Policy System

| Module | Responsibility |
|--------|---------------|
| `policy_core/base_policy.py` | `BasePolicy` — shared base with config helpers and singleton safety checks |
| `policy_core/openai_interface.py` | `OpenAIPolicyInterface` — abstract hooks for OpenAI-format request/response |
| `policy_core/anthropic_execution_interface.py` | `AnthropicExecutionInterface` — execution-oriented Anthropic policy contract |
| `policy_core/policy_context.py` | `PolicyContext` — per-request mutable state (transaction ID, emitter, typed state slots) |
| `policy_core/streaming_policy_context.py` | `StreamingPolicyContext` — streaming-specific context (egress queue, stream state, keepalive) |
| `policy_core/policy_protocol.py` | `PolicyProtocol` — structural typing protocol for policy infrastructure |
| `policy_manager.py` | Runtime policy loading, hot-swapping, DB persistence |
| `policies/` | Concrete policy implementations |

### Streaming Infrastructure

| Module | Responsibility |
|--------|---------------|
| `orchestration/policy_orchestrator.py` | `PolicyOrchestrator` — wires PolicyExecutor + ClientFormatter + queues |
| `streaming/policy_executor/` | Chunk assembly, policy hook dispatch, timeout management |
| `streaming/client_formatter/` | ModelResponse -> SSE string conversion |
| `streaming/stream_blocks.py` | `ContentStreamBlock`, `ToolCallStreamBlock` — accumulated block types |
| `streaming/stream_state.py` | `StreamState` — tracks blocks, finish reason, raw chunks during streaming |

### Storage & Observability

| Module | Responsibility |
|--------|---------------|
| `storage/persistence.py` | `ConversationEvent` model, DB writes, Redis pub/sub |
| `observability/emitter.py` | `EventEmitter` — fire-and-forget event recording (DB + Redis + stdout) |
| `observability/transaction_recorder.py` | Records request/response pairs for conversation history |

### Other

| Module | Responsibility |
|--------|---------------|
| `llm/litellm_client.py` | OpenAI-format backend calls via LiteLLM |
| `llm/anthropic_client.py` | Anthropic-format backend calls via native SDK |
| `admin/` | Runtime policy management API (`/api/admin/*`) |
| `ui/` | Activity monitor, diff viewer (`/activity/*`, `/diffs`) |
| `config/` | YAML policy config loading |

## Key Abstractions

### BasePolicy

All policies inherit from `BasePolicy`. It provides:
- `short_policy_name` for identification
- `get_config()` for serializing policy configuration
- `freeze_configured_state()` — load-time check that rejects mutable containers on the instance (policies are singletons shared across concurrent requests)

### OpenAIPolicyInterface

Defines hooks for the OpenAI format pipeline. Two required hooks for non-streaming:
- `on_openai_request(request, context) -> request`
- `on_openai_response(response, context) -> response`

Plus streaming hooks that fire as chunks arrive: `on_chunk_received`, `on_content_delta`, `on_content_complete`, `on_tool_call_delta`, `on_tool_call_complete`, `on_finish_reason`, `on_stream_complete`, `on_streaming_policy_complete`.

### AnthropicExecutionInterface

An execution-oriented contract where the policy drives the entire request lifecycle:

```python
def run_anthropic(self, io: AnthropicPolicyIOProtocol, context: PolicyContext) -> AsyncIterator[AnthropicPolicyEmission]:
```

The policy receives an `io` object with `complete()` and `stream()` methods. It can call the backend zero or more times and yields outbound events for the client. This design supports multi-turn patterns (e.g., an overseer that calls the backend, inspects tool use, then decides whether to proceed).

### PolicyContext

Created per-request. Carries:
- `transaction_id` — unique ID for this request/response cycle
- `emitter` — fire-and-forget event recording
- `get_request_state(owner, type, factory)` — typed per-policy state keyed by `(policy_instance, type)`, preventing key collisions between policies
- `session_id` — optional client session tracking
- Span helpers for OpenTelemetry tracing

### SimplePolicy

A convenience base class that buffers streaming content and surfaces three simple override points:

```python
async def simple_on_request(self, request_str: str, context) -> str
async def simple_on_response_content(self, content: str, context) -> str
async def simple_on_response_tool_call(self, tool_call, context) -> tool_call
```

Supports both OpenAI and Anthropic formats. Trades streaming responsiveness for implementation simplicity.

## Data Model

### Conversation Events

Each request/response cycle produces `ConversationEvent` records:

```
conversation_calls: one row per transaction
  - call_id (PK), model_name, status, created_at, completed_at

conversation_events: request and response events per call
  - call_id (FK), event_type ("request" | "response"), payload (JSONB), created_at
```

Events store both original (pre-policy) and final (post-policy) data, enabling diff views.

### Policy Persistence

The active policy is stored in `current_policy`:

```
current_policy: single-row table (id=1)
  - policy_class_ref (e.g. "luthien_proxy.policies.noop_policy:NoOpPolicy")
  - config (JSONB), enabled_at, enabled_by
```

Policy changes are protected by a Redis distributed lock.

## How to Add a New Policy

1. Create `src/luthien_proxy/policies/my_policy.py`
2. Choose your base class:
   - **`SimplePolicy`** — easiest: override `simple_on_request`, `simple_on_response_content`, `simple_on_response_tool_call`. Works with both OpenAI and Anthropic formats.
   - **`BasePolicy` + `OpenAIPolicyInterface` + `AnthropicExecutionInterface`** — full control over streaming and both API formats.
3. Add a Pydantic config model if your policy needs configuration
4. Enable via YAML config or the admin API:

```yaml
# config/policy_config.yaml
policy:
  class: "luthien_proxy.policies.my_policy:MyPolicy"
  config:
    some_setting: "value"
```

Or at runtime:
```bash
curl -X POST http://localhost:8000/api/admin/policy/enable \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -d '{"policy_class_ref": "luthien_proxy.policies.my_policy:MyPolicy", "config": {}}'
```

### Policy Rules

- Policies are **singletons** — never store request-scoped data on `self`. Use `context.get_request_state()`.
- Config-time collections must be immutable (`tuple`, `frozenset`). `freeze_configured_state()` enforces this.
- For `SimplePolicy`, streaming content is buffered until block completion, then your hook runs on the complete content.
- For full `OpenAIPolicyInterface`, you control what gets pushed to the egress queue via `ctx.push_chunk()`.
