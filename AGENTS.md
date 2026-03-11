# PROJECT KNOWLEDGE BASE

**Generated:** 2026-03-02  
**Commit:** 027c945  
**Branch:** main

## OVERVIEW

AI Control gateway for LLMs. FastAPI proxy between Claude Code/Codex and LLM APIs. Intercepts every request/response, applies configurable policies (block, transform, judge), logs everything. Python 3.13 + LiteLLM + asyncio.

## STRUCTURE

```
luthien-proxy/
├── src/luthien_proxy/        # Core package (see src/luthien_proxy/AGENTS.md)
│   ├── main.py               # SOLE entry point: create_app() + uvicorn
│   ├── gateway_routes.py     # /v1/chat/completions, /v1/messages
│   ├── pipeline/             # Request lifecycle orchestration (OpenAI + Anthropic)
│   ├── orchestration/        # PolicyOrchestrator: wires PolicyExecutor ↔ ClientFormatter
│   ├── policies/             # Concrete policy implementations (see policies/AGENTS.md)
│   ├── policy_core/          # Neutral contract layer (see policy_core/AGENTS.md)
│   ├── streaming/            # Queue-based streaming pipeline (see streaming/AGENTS.md)
│   ├── llm/                  # LiteLLM + Anthropic SDK wrappers, Pydantic type models
│   ├── observability/        # EventEmitter → stdout/Postgres/Redis + OTel spans
│   ├── storage/              # ConversationEvent persistence (background queue)
│   ├── admin/                # Runtime policy CRUD API (/api/admin/policy/*)
│   ├── history/              # Conversation history API + UI (/history/*)
│   ├── request_log/          # HTTP-level request logging
│   ├── debug/                # Debug inspection endpoints
│   ├── ui/                   # Activity monitor, policy config UI, diff viewer
│   ├── utils/                # DB pool, Redis helpers, constants (no __init__.py)
│   └── static/               # HTML/JS/CSS served via FastAPI StaticFiles
├── saas_infra/               # Railway provisioning CLI (SEPARATE package, outside src/)
├── tests/
│   ├── unit_tests/           # Fast, network-blocked, 3s timeout (see CLAUDE.md there)
│   ├── integration_tests/    # Needs running Postgres
│   └── e2e_tests/            # Needs full stack, slow (see CLAUDE.md there)
├── migrations/               # Hand-written SQL (000-008), hash-validated, NOT Alembic
├── scripts/                  # quick_start.sh, dev_checks.sh, format_all.sh, etc.
├── docker/                   # Dockerfile.gateway (Python 3.13 + Rust), standalone, migrations
├── config/                   # policy_config.yaml (loaded via POLICY_CONFIG env var)
└── dev/                      # OBJECTIVE.md, NOTES.md, TODO.md, context/ (persistent knowledge)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add a policy | `src/luthien_proxy/policies/` | Subclass `SimplePolicy` for content transforms |
| Understand request flow | `pipeline/processor.py` (OpenAI), `pipeline/anthropic_processor.py` | Two completely different execution models |
| Policy interfaces | `policy_core/openai_interface.py`, `policy_core/anthropic_execution_interface.py` | OpenAI = hooks, Anthropic = execution-oriented |
| Streaming internals | `streaming/policy_executor/executor.py` | Chunk assembly + policy hook invocation |
| SSE formatting | `streaming/client_formatter/` | ModelResponse → `data: {json}\n\n` |
| Add admin endpoint | `admin/routes.py` | Requires `ADMIN_API_KEY` auth |
| Add UI page | `ui/routes.py` + `static/` | HTML served from static/, Alpine.js frontend |
| Type definitions | `llm/types/openai.py`, `llm/types/anthropic.py` | Pydantic models for both API formats |
| DI container | `dependencies.py` | `Dependencies` dataclass, FastAPI `Depends` |
| Env config | `settings.py` | Pydantic Settings, all env vars defined here |
| DB schema | `migrations/` | Raw SQL, hash-validated — never modify applied migrations |
| Dev workflow | `CLAUDE.md` | Objective workflow, PR rules, commands |
| Architecture docs | `dev/REQUEST_PROCESSING_ARCHITECTURE.md` | Full request lifecycle documentation |
| Gotchas | `dev/context/gotchas.md` | 30+ documented wrong/right patterns |
| Technical decisions | `dev/context/decisions.md` | Why we chose X over Y |

## CODE MAP (Critical Symbols)

| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `create_app()` | function | `main.py` | FastAPI app factory (sole entry point) |
| `Dependencies` | dataclass | `dependencies.py` | DI container: policy, DB, Redis, LLM client |
| `OpenAIPolicyInterface` | ABC | `policy_core/openai_interface.py` | 10 abstract hooks for OpenAI streaming |
| `AnthropicExecutionInterface` | Protocol | `policy_core/anthropic_execution_interface.py` | `run_anthropic(io, ctx)` — policy owns execution |
| `SimplePolicy` | class | `policies/simple_policy.py` | Convenience base: buffers streaming, exposes 3 override points |
| `PolicyContext` | dataclass | `policy_core/policy_context.py` | Request-scoped mutable state + typed policy state API |
| `PolicyOrchestrator` | class | `orchestration/policy_orchestrator.py` | Wires PolicyExecutor ↔ ClientFormatter via queues |
| `PolicyExecutor` | class | `streaming/policy_executor/executor.py` | Chunk assembly + policy hook invocation + timeout |
| `StreamingChunkAssembler` | class | `streaming/streaming_chunk_assembler.py` | Raw chunks → ContentStreamBlock / ToolCallStreamBlock |
| `EventEmitter` | class | `observability/emitter.py` | Fire-and-forget to stdout/Postgres/Redis |
| `process_llm_request()` | function | `pipeline/processor.py` | Full OpenAI request lifecycle |
| `process_anthropic_request()` | function | `pipeline/anthropic_processor.py` | Full Anthropic request lifecycle |

## CONVENTIONS (Deviations from Standard Python)

- **Line length 120**, double quotes, Ruff formatter (not black)
- **`PLC0415` enforced**: no function-level imports (tests exempt)
- **Google-style docstrings** enforced via ruff `D` rules (non-gating)
- **`beartype`** for optional runtime type checks in critical sections
- **f-strings only** — never `.format()` or `%`
- **`logging` only** — never `print()` for debugging
- **`asyncio.Queue.shutdown()`** for stream termination, not `None` sentinels
- **Bounded queues** (maxsize=10000) with 30s timeout on `put()` as circuit breaker
- **Pyright basic mode** — only checks `src/` and `saas_infra/`, NOT `tests/`
- **asyncio_mode = "auto"** — no `@pytest.mark.asyncio` needed
- **`saas_infra/`** lives outside `src/` — tests need `sys.path` hack to import it
- **`utils/`** has no `__init__.py` — import modules directly

## ANTI-PATTERNS (THIS PROJECT)

- **Policy instances must be stateless** — mutable request data → `PolicyContext.get_policy_state()`, never on policy object. Public mutable containers (`dict`/`list`/`set`) rejected at load time.
- **`on_streaming_policy_complete()` must NOT emit chunks** — cleanup only (buffers, caches, state)
- **Content + finish_reason in same chunk** — finish_reason silently ignored. Always emit as separate chunks.
- **Thinking blocks must precede text** in Anthropic responses — wrong order = API error
- **Don't overload fields** — add new typed fields, never `str | list | dict | Any`
- **Streaming/non-streaming parity** — any feature must work identically in both paths
- **One PR = One Concern** — bug fix during feature work? Separate PR.
- **Never modify applied migrations** — hash validation will catch drift

## COMMANDS

```bash
# Full dev checks (format + lint + type check + tests)
./scripts/dev_checks.sh

# Quick format
./scripts/format_all.sh

# Unit tests only (fast, default pytest run)
uv run pytest tests/unit_tests

# E2E tests (slow, needs full stack)
uv run pytest -m e2e -x -v

# Type check
uv run pyright

# Start local stack (auto-port selection)
./scripts/quick_start.sh

# Restart gateway after code changes
docker compose restart gateway

# DB inspection
uv run python scripts/query_debug_logs.py
```

## NOTES

- **Two completely different API paths**: OpenAI (hook-based policies) vs Anthropic (execution-oriented policies). Policies must implement both.
- **LiteLLM >= 1.81.0** has breaking streaming changes — `response_normalizer.py` patches them.
- **Docker mounts src/ as read-only** — Python changes need `docker compose restart gateway`.
- **`quick_start.sh` ≠ `docker compose up`** — quick_start handles port selection and project naming.
- **Pre-commit includes beartype smoke check** — catches type annotation errors before commit.
- **Unit tests block ALL network sockets** via `conftest.py` monkeypatch — no opt-out per test.
