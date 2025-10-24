# Codebase Learnings

Architectural patterns, module relationships, and how subsystems work together.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points or prose).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Architecture Overview (2025-10-08)

- **Control Plane** (`src/luthien_proxy/control_plane/`): FastAPI application that makes policy decisions
- **Proxy** (`src/luthien_proxy/proxy/`): LiteLLM proxy integration with custom logging
- **Policies** (`src/luthien_proxy/policies/`): Policy implementations that receive callbacks from the proxy
- **Operational Helpers** (`scripts/`): Utility scripts such as `run_bg_command.sh` (fire-and-poll shell launcher); UI test harnesses now live in the e2e suite.

Centralized control plane makes policy decisions, proxy stays thin and forwards callbacks.

## Key Patterns (2025-10-08)

- Structured conversation storage: `conversation_calls`, `conversation_events`, and `conversation_tool_calls` tables capture canonical history. APIs now read from these tables instead of replaying `debug_logs`, while Redis still handles live SSE fan-out.

(Add additional patterns as discovered during development with timestamps.)

## Conversation Turn Roadmap (2025-10-09)

- Control-plane ingestion already sees normalized OpenAI-style payloads (requests + completions) via `unified_callback`; future work will derive canonical conversation turns directly from these events.
- Planned storage evolution: replace per-tool/judge tables with per-turn records (`conversation_turns`) plus optional policy annotations, linked into threads for branching and hashed via user-history + assistant-final text.
- Live monitor v2 targets chat-style rendering by streaming turns (request originals vs. final policy output) and inline tool-call details from the same structured records.

## Documentation Structure (2025-10-10)

**Major reorganization completed** - consolidated redundant dataflow docs into three focused files:

- **`docs/ARCHITECTURE.md`** (126 lines): Architectural decisions, component details, data storage overview. Focus on "why" and "what exists."
- **`docs/diagrams.md`** (300 lines): Single source of truth for all visual diagrams (flowcharts, sequence diagrams, comparison tables). Includes sequence diagram showing hook call timing.
- **`docs/developer-onboarding.md`** (294 lines): Learning path for new developers with hook flows, code reading path, policy examples (including ToolCallBufferPolicy), FAQ, and data structures.

**Key changes from previous structure:**
- Eliminated diagram duplication (was in both reading-guide and dataflow-diagrams)
- Removed redundant hook flow text descriptions (diagrams are clearer)
- Preserved all valuable content: JSON examples, ToolCallBufferPolicy walkthrough, architectural rationale
- Clear separation of concerns: architecture (why) vs diagrams (visual) vs onboarding (how-to-learn)

**Rationale documented in:** `dev/archive/2025-10-10_revised_plan_d_dataflow_docs.md`

## LiteLLM Role in V2 Architecture (2025-10-17)

**Key insight**: LiteLLM should ONLY be used for API format conversion, not parameter validation.

**Problem discovered**: When passing model-specific parameters (e.g., `verbosity: "low"` for GPT-5 reasoning models), litellm's `acompletion()` was rejecting them with "Unknown parameter" errors, even though the underlying provider supports them.

**Solution**: Use litellm's `allowed_openai_params` mechanism to forward unknown parameters:
- In Python SDK: Pass `allowed_openai_params=["param_name"]` to `acompletion()` calls
- In proxy config: Add `allowed_openai_params: ["param_name"]` to `litellm_settings`
- **Note**: Config option only works for the proxy server, not direct SDK calls

**Implementation** ([src/luthien_proxy/v2/main.py:152-157](src/luthien_proxy/v2/main.py#L152-L157)):
```python
# Identify any model-specific parameters to forward
known_params = {"verbosity"}  # Add more as needed
model_specific_params = [p for p in data.keys() if p in known_params]
if model_specific_params:
    data["allowed_openai_params"] = model_specific_params
```

This allows litellm to pass through parameters it doesn't recognize, letting each provider handle its own model-specific parameters (e.g., `verbosity` for GPT-5, `reasoning_effort` for o1, etc.).

**Key principle**: We want litellm to do format conversion (OpenAI ↔ Anthropic ↔ etc.) but NOT to validate or filter parameters. Each provider knows best what parameters it supports.

## E2E Test Infrastructure (2025-10-17)

**Self-contained test servers**: E2E tests now manage their own server instances without interfering with dev environment.

**V2GatewayManager** ([tests/e2e_tests/helpers/v2_gateway.py](tests/e2e_tests/helpers/v2_gateway.py)):
- Uses `multiprocessing.Process` to run V2 gateway in isolated subprocess
- Runs on dedicated test port (8888) separate from dev environment (8000)
- Handles startup, health checking, and cleanup automatically
- Configured via pytest fixture with module scope for efficiency

**Key benefits**:
- Tests can run without manual server setup
- No port conflicts with dev environment
- Clean separation between test and dev infrastructure
- Test-specific configuration (API keys, ports) isolated from production settings

**Usage pattern**:
```python
@pytest.fixture(scope="module")
def v2_gateway(e2e_settings: E2ESettings):
    manager = V2GatewayManager(port=8888, api_key="sk-test-v2-gateway", verbose=e2e_settings.verbose)
    with manager.running():
        yield manager
```

This pattern follows the existing e2e infrastructure for control plane and dummy provider management.
