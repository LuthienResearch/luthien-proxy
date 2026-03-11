# src/luthien_proxy — Core Package

## OVERVIEW

Integrated FastAPI gateway. Single process handles API proxying, policy enforcement, streaming, persistence, and UI.

## MODULE MAP

```
luthien_proxy/
├── main.py                 # App factory + uvicorn runner
├── gateway_routes.py       # API routes: /v1/chat/completions, /v1/messages
├── dependencies.py         # Dependencies dataclass (DI container) + FastAPI Depends
├── settings.py             # Pydantic Settings — all env vars
├── config.py               # YAML policy loading + dynamic class import
├── policy_manager.py       # Runtime policy CRUD (DB + file + Redis locking)
├── policy_composition.py   # Wraps policies into MultiSerialPolicy
├── credential_manager.py   # Passthrough auth + API key validation
├── auth.py                 # Admin token + session verification
├── session.py              # Login/logout + cookie sessions
├── telemetry.py            # OTel setup (tracing, logging, instrumentation)
├── exceptions.py           # BackendAPIError + LiteLLM error mapping
├── types.py                # Shared type aliases (JSONObject, RawHttpRequest)
│
├── pipeline/               # REQUEST PROCESSING (two paths)
│   ├── processor.py        #   OpenAI: process_llm_request()
│   ├── anthropic_processor.py  # Anthropic: process_anthropic_request()
│   ├── client_format.py    #   ClientFormat enum (OPENAI | ANTHROPIC)
│   └── session.py          #   Session ID extraction from headers/body
│
├── orchestration/          # STREAMING COORDINATION
│   └── policy_orchestrator.py  # PolicyOrchestrator (wires executor ↔ formatter)
│
├── policy_core/            # NEUTRAL CONTRACT LAYER → see policy_core/AGENTS.md
├── policies/               # CONCRETE POLICIES → see policies/AGENTS.md
├── streaming/              # STREAMING PIPELINE → see streaming/AGENTS.md
│
├── llm/                    # LLM CLIENT LAYER
│   ├── client.py           #   LLMClient ABC
│   ├── litellm_client.py   #   LiteLLMClient (OpenAI-format via LiteLLM)
│   ├── anthropic_client.py #   AnthropicClient (native SDK)
│   ├── response_normalizer.py  # Patches LiteLLM >= 1.81.0 streaming breakage
│   └── types/              #   Pydantic models for OpenAI + Anthropic formats
│
├── observability/          # TELEMETRY + EVENT SYSTEM
│   ├── emitter.py          #   EventEmitter → stdout/Postgres/Redis (fire-and-forget)
│   ├── redis_event_publisher.py  # Redis pub/sub for real-time UI
│   └── transaction_recorder.py   # Buffers ingress/egress, reconstructs responses
│
├── storage/                # PERSISTENCE
│   ├── persistence.py      #   ConversationEvent model + DB writes (partially dead code)
│   └── events.py           #   reconstruct_full_response_from_chunks
│
├── admin/                  # ADMIN API (requires ADMIN_API_KEY)
│   ├── routes.py           #   /api/admin/policy/* endpoints
│   └── policy_discovery.py #   Discover available policy classes (uses eval — security-sensitive)
│
├── history/                # CONVERSATION HISTORY
│   ├── routes.py           #   /history/* + /api/history/*
│   ├── service.py          #   Session queries (674 lines — largest service file)
│   └── models.py           #   SessionSummary, ConversationTurn, etc.
│
├── request_log/            # HTTP-LEVEL LOGGING
│   ├── recorder.py         #   RequestLogRecorder
│   ├── service.py, routes.py, models.py, sanitize.py
│
├── debug/                  # DEBUG ENDPOINTS
│   ├── routes.py, service.py, models.py
│
├── ui/                     # UI ROUTES (HTML pages)
│   └── routes.py           #   /activity/*, /policy-config, /diffs, /history
│
└── utils/                  # SHARED UTILITIES (no __init__.py)
    ├── constants.py        #   All magic numbers/strings
    ├── db.py               #   DatabasePool
    ├── redis_client.py     #   Redis helpers
    └── migration_check.py  #   Verify DB migrations at startup
```

## DEPENDENCY GRAPH (directed, by import frequency)

```
policy_core  ←── policies, streaming, orchestration, pipeline, gateway_routes
     (central contract layer — most-imported package)

llm/types    ←── policy_core, policies, pipeline, orchestration
     (type definitions flow outward)

utils        ←── nearly everything
     (true utility layer)

observability ←── pipeline, orchestration, streaming, policy_core
     (EventEmitter used broadly)

dependencies ←── all route modules + main
     (DI container)

settings     ←── main, config, telemetry, admin, policies
     (env config)
```

**Layering rule**: `policy_core` is neutral — `policies` depend on it but never the reverse. `streaming` depends on `policy_core` but not `policies`. Route modules (`admin`, `debug`, `ui`, `history`, `request_log`) depend on `dependencies` for DI and `auth` for access control.

## WHERE TO LOOK

| Task | Start Here | Then |
|------|-----------|------|
| Trace OpenAI request end-to-end | `gateway_routes.py` | `pipeline/processor.py` → `orchestration/` → `streaming/` |
| Trace Anthropic request | `gateway_routes.py` | `pipeline/anthropic_processor.py` (policy drives execution) |
| Add new env var | `settings.py` | Then `dependencies.py` if DI-injected |
| Add new API endpoint | Create router in appropriate module | Register in `main.py` |
| Change DB schema | Add `migrations/NNN_*.sql` | Never modify existing migration files |
| Debug event flow | `observability/emitter.py` | `transaction_recorder.py` for request/response recording |
| Understand auth | `auth.py` + `credential_manager.py` | `session.py` for cookie-based sessions |

## ANTI-PATTERNS (THIS MODULE)

- **Module-level side effects in `main.py`**: `configure_tracing()`, `configure_logging()`, `instrument_redis()` execute at import time, before `create_app()`.
- **`admin/policy_discovery.py` uses `eval()`** — suppressed with `noqa: S307`. Security-sensitive.
- **`storage/persistence.py`** has partially dead code (`build_conversation_events`, `record_conversation_events` have no runtime callsites). Flagged in TODO.md.
- **Multi-policy files have 17 `type: ignore` suppressions** combined — policy composition types need refinement.
