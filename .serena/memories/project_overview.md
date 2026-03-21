# Luthien Proxy — Project Overview

## Purpose
AI Control gateway for LLMs. FastAPI proxy between Claude Code/Codex and LLM APIs (Anthropic + OpenAI).
Intercepts every request/response, applies configurable policies (block, transform, judge), logs everything.

## Tech Stack
- **Python 3.13** (requires-python >= 3.13)
- **FastAPI** — web framework, sole entry point: `create_app()` in `main.py`
- **LiteLLM** (>= 1.81.0) — upstream LLM calls (OpenAI + Anthropic)
- **asyncpg** — PostgreSQL async driver
- **Redis** — real-time activity streaming, pub/sub
- **OpenTelemetry** — distributed tracing (Tempo backend)
- **Pydantic** — settings, type models
- **beartype** — optional runtime type checks
- **uv** — package manager and build system
- **Docker** — deployment (docker-compose with postgres, redis, gateway, migrations)
- **Ruff** — linting + formatting
- **Pyright** — type checking (basic mode)
- **pytest** — testing (asyncio_mode=auto, 3s timeout for unit tests)

## Key Architecture
- **Two API paths**: OpenAI (hook-based policies) vs Anthropic (execution-oriented policies)
- **SimplePolicy** base class: buffers streaming, exposes 3 override points
- **Queue-based streaming**: PolicyExecutor → bounded queue (maxsize=10000) → ClientFormatter
- **Event-driven observability**: EventEmitter → stdout/Postgres/Redis + OTel spans
- **Hand-written SQL migrations** (000-008), hash-validated, NOT Alembic

## Repository Structure
```
src/luthien_proxy/        # Core package
├── main.py               # SOLE entry point: create_app()
├── gateway_routes.py     # /v1/chat/completions, /v1/messages
├── pipeline/             # Request lifecycle (OpenAI + Anthropic processors)
├── orchestration/        # PolicyOrchestrator: wires PolicyExecutor ↔ ClientFormatter
├── policies/             # Concrete policy implementations
├── policy_core/          # Neutral contract layer (interfaces)
├── streaming/            # Queue-based streaming pipeline
├── llm/                  # LiteLLM + Anthropic SDK wrappers, Pydantic types
├── observability/        # EventEmitter → stdout/Postgres/Redis + OTel
├── storage/              # ConversationEvent persistence
├── admin/                # Runtime policy CRUD API
├── history/              # Conversation history API + UI
├── ui/                   # Activity monitor, policy config UI, diff viewer
├── utils/                # DB pool, Redis helpers (no __init__.py)
└── static/               # HTML/JS/CSS
tests/
├── unit_tests/           # Fast, network-blocked, 3s timeout
├── integration_tests/    # Needs Postgres
└── e2e_tests/            # Needs full stack
migrations/               # Hand-written SQL, hash-validated
saas_infra/               # Railway provisioning CLI (SEPARATE package)
config/                   # policy_config.yaml
scripts/                  # Dev scripts
```
