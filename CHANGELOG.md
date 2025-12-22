# CHANGELOG

## Unreleased | TBA

- Add public demo deployment solution (#slack-add-demo-deployment)
  - Fly.io deployment with `fly.toml` and automated deploy script
  - Render Blueprint (`render.yaml`) for one-click GitHub deployment
  - Railway deployment configuration (`railway.toml`, `railway.json`)
  - DigitalOcean App Platform spec (`deploy/digitalocean/app.yaml`)
  - Production Docker Compose with Caddy reverse proxy for automatic HTTPS
  - VPS deployment script with Docker setup and secure credential generation
  - Comprehensive deployment documentation in `deploy/README.md`

- Add conversation history viewer with styled message types and markdown export (conversation-history-viewer)
  - Browse recent sessions at `/history` with turn counts, policy interventions, and model usage
  - View full conversation detail at `/history/session/{id}` with message type styling (system/user/assistant/tool call/tool result)
  - Policy annotations shown inline on turns that had interventions
  - Export any session to markdown via `/history/api/sessions/{id}/export`

- Increase unit test coverage from 84% to 90% (#115)
- Fix validation error when images in Anthropic requests (#103, #104)
- Migration validation and fail-fast checks (#110)
  - `run-migrations.sh` validates DB state against local files before applying
  - Gateway startup check ensures all migrations are applied
  - Fails fast with clear errors if: migrations missing locally, unapplied migrations, or hash mismatch
  - Records content_hash for each migration to detect modifications

- Improve login page UX (dogfooding-login-ui-quick-fixes)
  - Add show/hide password toggle below input field (avoids conflict with password managers)
  - Add clickable dev key hint for development environments
  - Add guidance for production users to check .env or contact admin
- Structured span hierarchy for request processing (luthien-proxy-a0r)
  - All pipeline phases (process_request, policy_on_request, send_upstream, process_response) are now visible as siblings in Grafana/Tempo
  - Add `luthien.policy.name` attribute to root span for easy policy identification
  - Add `request_summary` and `response_summary` fields to PolicyContext for policy-defined observability

- Dependency injection for `create_app()` (#105)

- Session ID tracking for conversation context (#102)
  - Extract session ID from Anthropic `metadata.user_id` (Claude Code format: `user_<hash>_account__session_<uuid>`)
  - Extract session ID from `x-session-id` header (OpenAI format)
  - Persist session ID to database for querying conversations by session
  - Add `RawHttpRequest` dataclass to capture original HTTP request data
  - Add OpenTelemetry span attributes for session tracking (`luthien.session_id`)
  - Debug API now returns session_id in call listings and event responses

- Unify OpenAI and Anthropic endpoint processing (#92)
- Fix broken migration script that prevented migrations from running (#fix-migration-script)
- Replace magic numbers with named constants [constants.py](src/luthien_proxy/utils/constants.py)

- Session-based login for browser access to admin/debug UIs (#88)
  - Add `/login` page with session cookie authentication
  - Protected UI pages (`/activity/monitor`, `/debug/diff`, `/policy-config`) redirect to login when unauthenticated
  - Sign out links on all protected pages
  - Backwards compatible: API endpoints still accept Bearer token and x-api-key

- Confirmed policy config UI backend integration already complete via PR #66 (feature/policy-ui-backend)

- Centralize environment configuration with pydantic-settings (#refactor/env-config-centralize)
  - Add `Settings` class in `src/luthien_proxy/settings.py` for typed configuration
  - Replace scattered `os.getenv()` calls throughout codebase with centralized settings access
  - Support `.env` file loading via pydantic-settings
  - Add `clear_settings_cache()` for test isolation

- Remove unused prisma dependency (#84)
- Added auth to debug endpoints (#86)
- Inject EventEmitter via DI instead of global state (#dependency_injection)
- Added e2e tests that actually invoke claude code running through the proxy

- Codebase cleanup (#81)
  - Remove dead code: `control_plane/` (stale pycache), `streaming_aggregation.py`
  - Standardize on Python module docstrings (removed ABOUTME convention)
  - Organize and deduplicate TODO.md
  - Update CLAUDE.md and codebase_learnings.md to reflect actual module structure

- Implement trace (tempo) + log (loki) observability

- Add `on_streaming_policy_complete()` lifecycle hook for cleanup (#76)
  - New policy hook called in finally block after all streaming policy processing completes
  - Guarantees cleanup runs even if errors occurred during policy processing
  - Implement buffer cleanup in ToolCallJudgePolicy using new hook
  - Simplify `_validate_tool_call_for_judging()` to return just the tool_call dict

- Streaming and Anthropic client fixes (#75)
  - Fix streaming tool calls missing `message_delta` for Anthropic clients
  - Refactor `AnthropicSSEAssembler` to `streaming/client_formatter`
  - Explicitly implement `ClientFormatter` protocol
  - Fix `ChatCompletionMessageToolCall` typing
  - Remove model registration logic

- Fix ToolCallJudgePolicy inheritance to use BasePolicy instead of PolicyProtocol (#62)
  - Resolves gateway startup failure when ToolCallJudgePolicy is configured
  - Override `on_chunk_received()` to prevent duplicate token streaming bug
  - Fix test mock signature to match `call_judge()` parameters
- Dependency injection improvements (#dependency-injection)
  - Add `Dependencies` container class for centralized service management
  - Create FastAPI `Depends()` functions for type-safe dependency access
  - Derive `event_publisher` lazily from `redis_client` (no duplicate storage)
  - Create `LLMClient` once at startup instead of per-request instantiation
  - Replace `getattr(app.state, ...)` pattern with proper DI

- Observability improvements (#observability-refactor)
  - Refactored `LuthienPayloadRecord` → `PipelineRecord` with simplified all-primitive interface
  - Renamed `payload_type` → `pipeline_stage` for better semantics
  - Optimized label structure for efficient querying (only low-cardinality fields as labels, high-cardinality fields are structured metadata)
  - clarified observability functions; simplified implementations
  - Added utility scripts for Loki validation ([query_loki_fields.py](scripts/query_loki_fields.py), [test_line_format.py](scripts/test_line_format.py))

- Policy authoring improvements (#57)
  - Add `BasePolicy` class with default implementations and convenience methods
  - Add convenience properties to `StreamingPolicyContext` (`last_chunk_received`, `push_chunk()`, `transaction_id`, `request`, `scratchpad`)
  - Comprehensive test coverage for policy callbacks and streaming behavior (1100+ new test lines)

- Remove "v2" concept and consolidate architecture (#55)
  - Moved all code from `src/luthien_proxy/v2/*` to `src/luthien_proxy/*`
  - Updated all imports from `luthien_proxy.v2.*` to `luthien_proxy.*`
  - Renamed `V2_POLICY_CONFIG` env var to `POLICY_CONFIG`
  - Renamed `config/v2_config.yaml` to `config/policy_config.yaml`
  - Updated route prefixes: `/v2/debug` → `/debug`, `/v2/activity` → `/activity`
  - Renamed docker service from `v2-gateway` to `gateway`
  - Moved test directories from `tests/**/v2/` to `tests/**/`

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
  - Fixed service name references (control-plane → gateway)
  - Created `dev/ARCHITECTURE.md` with V2 core principles

#### Testing & Quality

- Comprehensive unit test coverage for policies, control plane, streaming orchestration
- Integration tests for V2 gateway endpoints
- End-to-end tests with real LLM providers (OpenAI, Anthropic, local Ollama)
- Docker-based testing with `./scripts/test_gateway.sh`
- Type safety with Pyright across all V2 modules

#### Developer Experience

- Single-command setup: `./scripts/quick_start.sh`
- Simplified service architecture: gateway, local-llm, db, redis
- Observability stack: `./scripts/observability.sh up -d`
- Live development with hot reload
- Launch scripts for Claude Code and Codex routing through gateway
- Comprehensive documentation:
  - `dev/event_driven_policy_guide.md` - Policy development guide
  - `dev/observability.md` - Observability features
  - `dev/VIEWING_TRACES_GUIDE.md` - Trace analysis walkthrough
  - `dev/OBSERVABILITY_DEMO.md` - Step-by-step demonstration

#### Configuration

- Single config file: `config/policy_config.yaml`
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
