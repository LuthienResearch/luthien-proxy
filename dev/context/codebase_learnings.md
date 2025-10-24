# Codebase Learnings

Architectural patterns, module relationships, and how subsystems work together.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points or prose).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## V2 Architecture Overview (2025-10-24)

- **V2 Gateway** (`src/luthien_proxy/v2/`): Integrated FastAPI + LiteLLM application with built-in policy enforcement
- **Control Plane** (`src/luthien_proxy/v2/control/`): Policy orchestration for request/response processing
- **Policies** (`src/luthien_proxy/v2/policies/`): Event-driven policy implementations
- **Storage** (`src/luthien_proxy/v2/storage/`): Conversation event persistence with background queue
- **Streaming** (`src/luthien_proxy/v2/streaming/`): Streaming pipeline and orchestration
- **Observability** (`src/luthien_proxy/v2/observability/`): OpenTelemetry integration for tracing

Integrated architecture - everything runs in single V2 gateway process.

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
def v2_gateway():
    manager = V2GatewayManager(port=8888, api_key="sk-test-v2-gateway")
    with manager.running():
        yield manager
```

---

(Add learnings as discovered during development with timestamps: YYYY-MM-DD)
