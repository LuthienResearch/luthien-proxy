# CHANGELOG

## Unreleased | TBA

- Cleanup and refactoring (#50)
  - introduced `policy_core` for common streaming/policy utilities
    - moved core abstractions (`PolicyProtocol`, `PolicyContext`, `StreamingPolicyContext` to `policy_core`)
  - split `policies/utils.py` into focused modules `chunk_builders.py`, `response_utils.py`, `tool_call_judge_utils.py`
  - dependency analysis script

## 0.0.2 | 2025-11-07

- **Anthropic streaming fixes** (post-#49):
  - Add `AnthropicSSEAssembler` for stateful SSE event generation with proper block indices
  - Fix `ToolCallJudgePolicy` streaming: add `on_content_delta()`, fix chunk creation with proper `Delta` and `StreamingChoices` types
  - Add `DebugLoggingPolicy` for inspecting streaming chunks
  - 8 regression tests to prevent streaming bugs

- Refactor streaming pipeline to explicit queue-based architecture (#49)
  - Simplified `PolicyOrchestrator.process_streaming_response` to clear 2-stage pipeline
  - PolicyExecutor: Block assembly + policy hooks with background timeout enforcement
  - **TimeoutMonitor**: Dedicated class for keepalive-based timeout tracking (100ms check interval)
    - Detects stalled streams when no chunks arrive within configured threshold
    - Raises `PolicyTimeoutError` with timing details for debugging
    - Automatic keepalive reset on each chunk processed
  - ClientFormatter: Model responses to client-specific SSE format (OpenAI/Anthropic)
  - Explicit typed queues (`Queue[ModelResponse]`, `Queue[str]`) define data contracts
  - Dependency injection pattern for policy execution and client formatting
  - Comprehensive unit tests (32 policy executor tests including 8 timeout enforcement tests, 12 formatter tests)
  - Transaction recording infrastructure at pipeline boundaries

- Add `SimpleEventBasedPolicy` for beginner-friendly policy authoring (buffers streaming into complete blocks)
  - Example policies: `SimpleUppercasePolicy`, `SimpleToolFilterPolicy`, `SimpleStringReplacementPolicy`
  - Comprehensive unit and e2e test coverage

### V2 Architecture Migration ([#46](https://github.com/LuthienResearch/luthien-proxy/pull/46))

**Massive cleanup**: Deleted ~9,735 lines of V1 code, tests, and documentation (48% reduction) while building out V2 architecture.

**Major architectural redesign** from separate LiteLLM proxy + control plane to integrated FastAPI + LiteLLM architecture with event-driven policies and comprehensive observability.

#### Core Architecture ([b04d6cd](../../commit/b04d6cd))

- Integrated V2 gateway combining API gateway, control logic, and LLM integration in single process
- `ControlPlaneService` protocol supporting both local and future networked implementations
- `PolicyHandler` abstraction with event-driven interface for user policies
- Bidirectional streaming with policy control over request/response transformation
- Format converters for OpenAI ↔ Anthropic API compatibility
- Support for both streaming and non-streaming responses

#### Event-Driven Policy System

- New `EventDrivenPolicy` DSL with lifecycle hooks:
  - `on_chunk_started`, `on_content_chunk`, `on_tool_call_chunk`, `on_chunk_completed`
  - `on_request_started`, `on_request_completed`
  - `on_response_started`, `on_response_completed`
- `PolicyContext` for per-request state management and event emission
- `StreamingOrchestrator` for managing streaming response pipelines with timeout handling
- Reference implementations:
  - `NoOpPolicy` / `EventBasedNoOpPolicy` - Pass-through for testing
  - `UppercaseNthWordPolicy` - Text transformation demo
  - `ToolCallJudgeV3Policy` - LLM-based tool call security analysis

#### Observability Infrastructure ([8480e06](../../commit/8480e06), [5882493](../../commit/5882493))

- **OpenTelemetry Integration**:
  - Distributed tracing with Grafana Tempo
  - Automatic span creation for all gateway, control plane, and streaming operations
  - Custom `luthien.*` span attributes (call_id, model, stream status, chunk counts, policy decisions)
  - Trace context propagation through entire request pipeline
  - Log correlation via trace_id/span_id injection
  - OTLP gRPC exporter to Tempo

- **Real-Time Monitoring**:
  - Activity stream via Server-Sent Events (SSE) at `/activity/stream`
  - Live activity monitor web UI at `/activity/monitor` with filtering by call_id/model/event_type
  - Redis pub/sub for real-time event distribution
  - Automatic event publishing for gateway, streaming, and policy lifecycle

- **Debug & Analysis Tools**:
  - Debug API at `/debug/`:
    - `/calls` - List recent calls
    - `/calls/{call_id}` - Get call details
    - `/calls/{call_id}/diff` - Compare original vs transformed content
  - Diff viewer UI at `/debug/diff` with side-by-side JSON comparison
  - Links to Grafana Tempo traces from all UIs

- **Grafana Dashboards**:
  - Live activity dashboard with auto-refresh (control plane logs, V2 API requests, policy activity, errors)
  - Metrics dashboard (request rate by model, p95 latency, latency breakdown, recent traces)
  - Pre-provisioned dashboards auto-loaded on Grafana startup

- **Log Collection**:
  - Grafana Loki for centralized logging
  - Promtail for Docker container log collection
  - 24-hour retention with aggressive compaction
  - Automatic trace ↔ log correlation

#### V1 Cleanup ([slash-and-burn](../../tree/slash-and-burn))

- **Deleted ~18,000 lines of V1 code**:
  - V1 control plane implementation (separate FastAPI service)
  - V1 proxy integration (separate LiteLLM process)
  - Old callback-based streaming system
  - Legacy policy interfaces and event models

- **Removed Docker services**:
  - `litellm-proxy` (port 4000) - replaced by integrated V2 gateway
  - `control-plane` (port 8081) - merged into V2 gateway
  - `dummy-provider` (port 4015) - test fixture no longer needed

- **Archived documentation** (15 files):
  - `dev/archive/`: 7 completed planning documents
  - `docs/archive/`: 4 V1 architecture guides (v1-reading-guide, v1-developer-onboarding, v1-diagrams, v1-ARCHITECTURE)
  - `config/archive/`: 5 V1 config files + policies directory

- **Deleted 16 obsolete scripts**:
  - V1-specific: `build_replay_examples.py`, `dummy_control_plane.py`, `export_replay_logs.sh`
  - Demo artifacts: `demo_*.py`, `run_demo*.sh`
  - One-off spikes: `test_anthropic_streaming.py`, `test_judge_streaming.py`, etc.

- **Removed infrastructure**:
  - `docker/Dockerfile.litellm` - V1 LiteLLM proxy image
  - 8 environment variables (LITELLM_MASTER_KEY, CONTROL_PLANE_URL, LUTHIEN_POLICY_CONFIG, etc.)
  - Replaced `LUTHIEN_POLICY_CONFIG` → `POLICY_CONFIG`

- **Updated documentation**:
  - Migrated policy configuration examples to EventDrivenPolicy DSL
  - Updated port references (8081 → 8000, removed 4000)
  - Fixed service name references (control-plane → v2-gateway)
  - Created `dev/ARCHITECTURE.md` with V2 core principles

#### Testing & Quality

- Comprehensive unit test coverage for policies, control plane, streaming orchestration
- Integration tests for V2 gateway endpoints
- End-to-end tests with real LLM providers (OpenAI, Anthropic, local Ollama)
- Docker-based testing with `./scripts/test_v2_gateway.sh`
- Type safety with Pyright across all V2 modules

#### Developer Experience

- Single-command setup: `./scripts/quick_start.sh`
- Simplified service architecture: v2-gateway, local-llm, db, redis
- Observability stack: `./scripts/observability.sh up -d`
- Live development with hot reload
- Launch scripts for Claude Code and Codex routing through V2 gateway
- Comprehensive documentation:
  - `dev/event_driven_policy_guide.md` - Policy development guide
  - `dev/observability-v2.md` - Observability features
  - `dev/VIEWING_TRACES_GUIDE.md` - Trace analysis walkthrough
  - `dev/OBSERVABILITY_DEMO.md` - Step-by-step demonstration

#### Configuration

- Single V2 config file: `config/policy_config.yaml`
- Policy selection via class path + config dict
- Environment variables consolidated in `.env.example`
- Docker Compose profiles for optional services (observability)

#### Performance & Reliability

- Streaming pipeline with configurable timeouts
- Redis for ephemeral state and pub/sub
- PostgreSQL with Prisma for persistent state
- Graceful error handling with span error recording
- Health checks for all services
- Connection pooling and async I/O throughout

---

## 0.0.1 | 2025-10-10

**Initial V1 implementation** (archived)

- Basic LiteLLM proxy integration with separate control plane
- Callback-based streaming system
- Initial policy engine with tool call judging
- Database persistence with debug logs
- Redis for caching and ephemeral state
- Demo UI for trace visualization
- Hook-based extensibility system
