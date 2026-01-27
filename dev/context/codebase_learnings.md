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
curl -X POST http://localhost:8000/admin/policy/create \
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
curl -X POST http://localhost:8000/admin/policy/activate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ADMIN_API_KEY}" \
  -d '{"name": "my-policy"}'
```

### Other Useful Endpoints

- `GET /admin/policy/current` - View active policy and its config
- `GET /admin/policy/instances` - List all saved policy instances
- `GET /admin/policy/list` - List available policy classes with descriptions

**Files**: `src/luthien_proxy/admin/routes.py`

---

## Request Flow and Format Conversions (2026-01-26)

Understanding where format conversions happen is critical for debugging. The 5-cycle debug in PR #134 revealed that issues can appear at any layer.

### Request Path (Client → LLM)

```
1. Client Request (OpenAI or Anthropic format)
         ↓
2. FastAPI endpoint (routes.py)
         ↓
3. Pydantic validation (llm/types/) ⚠️ Can reject valid provider-specific fields
         ↓
4. Format conversion (llm_format_utils.py) ⚠️ LOSSY - see below
         ↓
5. LiteLLM acompletion() → Provider API
```

### Response Path (LLM → Client)

```
1. Provider SSE stream
         ↓
2. LiteLLM ModelResponse chunks ⚠️ May reorder/transform fields
         ↓
3. PolicyExecutor + StreamingChunkAssembler (anthropic_sse_assembler.py)
         ↓
4. ClientFormatter (OpenAI or Anthropic SSE)
         ↓
5. Client receives SSE events
```

### Known Lossy Conversions

| Conversion | What Gets Lost/Changed | Impact |
|------------|----------------------|--------|
| Anthropic→OpenAI request | Thinking blocks were dropped | Fixed in PR #134 |
| Anthropic→OpenAI request | Images may be converted incorrectly | Issue #108 |
| LiteLLM streaming | `signature_delta` arrives after text starts | Requires delayed block closing |

### Debugging Checklist

When something breaks in the proxy, check these layers in order:

1. **Pydantic validation** - Is the input being rejected? Check `llm/types/`
2. **Format conversion** - Is data being dropped? Add logging to `llm_format_utils.py`
3. **LiteLLM behavior** - Is LiteLLM transforming something? Check their source
4. **Streaming assembler** - Is the response being assembled correctly? Check `anthropic_sse_assembler.py`
5. **Client formatter** - Is the output format correct for the client type?

### Key Files

- `src/luthien_proxy/llm/llm_format_utils.py` - Request/response format conversion
- `src/luthien_proxy/llm/anthropic_sse_assembler.py` - Streaming response assembly
- `src/luthien_proxy/llm/types/` - Pydantic models for validation
- `src/luthien_proxy/streaming/client_formatter/` - Output formatting

---

(Add learnings as discovered during development with timestamps: YYYY-MM-DD)
