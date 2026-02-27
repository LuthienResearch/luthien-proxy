# Codebase Learnings

Architectural patterns, module relationships, and how subsystems work together.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points or prose).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## V2 Architecture Overview (2025-10-24, updated 2025-12-04)

- **Gateway** (`src/luthien_proxy/`): Integrated FastAPI + LiteLLM application with built-in policy enforcement
- **Orchestration** (`src/luthien_proxy/orchestration/`): PolicyOrchestrator coordinates streaming pipeline
- **Policies** (`src/luthien_proxy/policies/`): Event-driven policy implementations
- **Policy Core** (`src/luthien_proxy/policy_core/`): Policy protocol, contexts, and chunk builders
- **Storage** (`src/luthien_proxy/storage/`): Conversation event persistence with background queue
- **Streaming** (`src/luthien_proxy/streaming/`): Policy executor and client formatters
- **Observability** (`src/luthien_proxy/observability/`): OpenTelemetry integration, transaction recording
- **Admin** (`src/luthien_proxy/admin/`): Runtime policy management API
- **Debug** (`src/luthien_proxy/debug/`): Debug endpoints for inspecting conversation events
- **UI** (`src/luthien_proxy/ui/`): Activity monitoring and diff viewer interfaces
- **LLM** (`src/luthien_proxy/llm/`): LiteLLM client wrapper and format converters

Integrated architecture - everything runs in single gateway process.

## Anthropic Runtime Model (2026-02-27)

- Anthropic request handling is execution-oriented, not hook-oriented.
- Policies on `/v1/messages` implement `AnthropicExecutionInterface.run_anthropic(io, context)` and emit responses/events.
- Policy runtime is backend-call agnostic: policies may make zero, one, or many backend calls via `io.complete()` / `io.stream()`.
- Legacy Anthropic compatibility helpers (`_handle_streaming`, `_handle_non_streaming` in `anthropic_processor.py`) were removed from production code.

## Key Patterns (2025-10-24)

- **Event-driven policies**: Policies implement lifecycle hooks (on_chunk_started, on_content_chunk, on_response_completed) instead of callbacks
- **Structured conversation storage**: `conversation_calls` and `conversation_events` tables capture request/response pairs
- **Background persistence**: `SequentialTaskQueue` ensures non-blocking event emission to database
- **OpenTelemetry**: Distributed tracing with automatic span creation and log correlation

## LiteLLM Role in V2 Architecture (2025-10-17)

**Key insight**: LiteLLM should ONLY be used for API format conversion, not parameter validation.

**Problem**: When passing model-specific parameters (e.g., `verbosity: "low"` for GPT-5), litellm's `acompletion()` rejects them with "Unknown parameter" errors.

**Solution**: Use litellm's `allowed_openai_params` mechanism:
```python
# Identify model-specific parameters to forward
known_params = {"verbosity"}  # Add more as needed
model_specific_params = [p for p in data.keys() if p in known_params]
if model_specific_params:
    data["allowed_openai_params"] = model_specific_params
```

**Key principle**: We want litellm to do format conversion (OpenAI ↔ Anthropic) but NOT validate parameters. Each provider knows what it supports.

## E2E Test Infrastructure (2025-10-17, updated 2025-10-24)

**Self-contained test servers**: E2E tests manage their own V2 gateway instances.

**V2GatewayManager** ([tests/e2e_tests/helpers/v2_gateway.py](../../tests/e2e_tests/helpers/v2_gateway.py)):
- Uses `multiprocessing.Process` to run V2 gateway in isolated subprocess
- Runs on dedicated test port (8888) separate from dev (8000)
- Handles startup, health checking, and cleanup automatically

**Usage pattern**:
```python
@pytest.fixture(scope="module")
def gateway():
    manager = V2GatewayManager(port=8888, api_key="sk-test-gateway")
    with manager.running():
        yield manager
```

## Streaming Pipeline Architecture (2025-11-05)

**Two-stage queue-based pipeline** with dependency injection and explicit data flow.

### Architecture

```
Backend LLM (via LiteLLM)
         ↓
AsyncIterator[ModelResponse] ← LiteLLM provides common format
         ↓
    PolicyExecutor (stage 1)
    - Block assembly (StreamingChunkAssembler)
    - Policy hook invocation
    - Timeout + keepalive monitoring
         ↓
policy_out_queue: Queue[ModelResponse]
         ↓
   ClientFormatter (stage 2)
    - OpenAI or Anthropic SSE conversion
         ↓
sse_queue: Queue[str]
         ↓
    Gateway yields to client
```

### Key Design Principles

1. **No Ingress Formatting**: LiteLLM already normalizes backend responses to ModelResponse format
2. **Dependency Injection**: Gateway instantiates PolicyExecutor and ClientFormatter, injects into PolicyOrchestrator
3. **Explicit Queues**: Typed queues (`Queue[ModelResponse]`, `Queue[str]`) define clear data contracts
4. **Context Threading**: `ObservabilityContext` and `PolicyContext` created at gateway, passed through entire lifecycle
5. **Bounded Queues**: maxsize=10000 with 30s timeout on put() operations (circuit breaker)
6. **Separation of Concerns**: Keepalive in PolicyExecutor (not PolicyContext), policies are stateless hooks

### Why This Architecture

**Simplified from original 3-stage plan**: Discovered LiteLLM provides ModelResponse, eliminating need for CommonFormatter stage.

**Benefits**:
- Clear data flow visible in code structure
- Type safety at queue boundaries
- Easy to test each stage in isolation
- Recording at boundaries via TransactionRecorder
- ~200 lines of unnecessary code eliminated

**Files**:
- `orchestration/policy_orchestrator.py` - orchestration (~30 lines)
- `streaming/policy_executor/` - block assembly + policy hooks (55 tests)
- `streaming/client_formatter/` - SSE conversion (12 tests)
- `policy_core/policy_context.py` - per-request state (transaction_id + scratchpad)

---

## Admin API for Policy Management (2025-11-20)

The gateway provides an admin API for runtime policy management. Policies are created as named instances and then activated.

### Authentication

All admin endpoints require `Authorization: Bearer ${ADMIN_API_KEY}` header.

### Creating and Activating Policies

**Step 1: Create a named policy instance** (saved to DB but not active):

```bash
curl -X POST http://localhost:8000/api/admin/policy/create \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}" \
  -d '{
    "name": "my-policy",
    "policy_class_ref": "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
    "config": {
      "model": "openai/gpt-4o-mini",
      "probability_threshold": 0.99,
      "temperature": 0.0,
      "max_tokens": 256
    },
    "description": "Optional description"
  }'
```

**Step 2: Activate the policy**:

```bash
curl -X POST http://localhost:8000/api/admin/policy/activate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}" \
  -d '{"name": "my-policy"}'
```

### Other Useful Endpoints

- `GET /api/admin/policy/current` - View active policy and its config
- `GET /api/admin/policy/instances` - List all saved policy instances
- `GET /api/admin/policy/list` - List available policy classes with descriptions

**Files**: `src/luthien_proxy/admin/routes.py`

---

## Don't Overload Fields — Add New Ones (2026-01-28)

**Principle**: When adding a new concept to an existing type, don't make an existing field more complex — add a separate field.

**Example**: Thinking blocks in `AssistantMessage`

- ❌ **PR #134**: Made `content: str | list[dict[str, Any]] | None` — now all code touching `content` must handle both cases, and `Any` loses type safety
- ✅ **PR #138**: Reverted `content` to `str | None`, added separate `thinking_blocks` field — existing code unchanged, new concept is isolated

**Why this matters**:
- Existing code can still make strong assumptions about the original field
- New concept gets its own type with proper validation
- `Any` types are a sign of imprecision — avoid them
- Same complexity, but more modular: logic for the new concept is factored out instead of spread across all field access points

**General rule**: If you're tempted to use `str | list | dict | Any` to handle multiple cases in one field, step back and consider whether each case deserves its own field.

---

## Streaming and Non-Streaming Parity (2026-01-31)

**Principle**: When implementing a feature for streaming responses, ensure the non-streaming path has equivalent behavior (and vice versa).

**Example**: [PR #147](https://github.com/LuthienResearch/luthien-proxy/pull/147) (SimplePolicy non-streaming fix)

- ❌ **Initial fix**: Added `on_response()` that called `simple_on_response_content()` for text — but forgot tool calls
- ✅ **Complete fix**: Also calls `simple_on_response_tool_call()` for each tool call, matching what the streaming path does in `on_tool_call_complete()`

**Why this matters**:
- Users expect consistent behavior regardless of `stream: true/false`
- Easy to forget one path when working on the other
- The streaming and non-streaming code paths are in different methods, so changes don't automatically propagate

**Checklist when modifying response processing**:
- [ ] Does the streaming path handle this? (`on_chunk_received`, `on_content_complete`, `on_tool_call_complete`)
- [ ] Does the non-streaming path handle this? (`on_response`)
- [ ] Are the transformations equivalent?

---

## Anthropic Streaming Lifecycle Parity Pattern (2026-02-27)

- Anthropic streaming now follows the same two-phase lifecycle as OpenAI streaming:
  - `on_anthropic_stream_complete(context)`: normal-completion hook
  - `on_anthropic_streaming_policy_complete(context)`: always-run cleanup hook (even on errors)
- `AnthropicStreamExecutor` mirrors OpenAI semantics:
  - calls `on_anthropic_stream_complete` after successful stream iteration
  - calls `on_anthropic_streaming_policy_complete` in `finally`
- Buffering convention for per-request policy state:
  - use framework-owned typed state: `PolicyContext.get_policy_state()` / `pop_policy_state()`
  - state `T` should be a dataclass with explicit fields (for strict typing)
  - `PolicyContext` scopes state by `(policy instance, state type)`; per-block maps live inside `T`
  - cleanup via `pop_policy_state()` in the always-run cleanup hook
- `PolicyContext` fields available to Anthropic hooks include:
  - `transaction_id`
  - `request` (OpenAI-format request when available)
  - `raw_http_request`
  - `session_id`
  - `scratchpad`
  - `request_summary` / `response_summary` for observability annotations

---

(Add learnings as discovered during development with timestamps: YYYY-MM-DD)
