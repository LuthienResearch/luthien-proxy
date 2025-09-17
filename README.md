# Luthien Control

Redwood-style AI Control as an LLM proxy for production agentic deployments.

## Quick Start

```bash
# 1. Start everything
./scripts/quick_start.sh

# 2. Test it works
uv run python scripts/test_proxy.py
```

You now have:
- **LiteLLM Proxy** at http://localhost:4000
- **Control Plane** at http://localhost:8081
- **PostgreSQL** and **Redis** fully configured

## Prerequisites

- Docker
- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Development

```bash
# After code changes, restart services
docker compose restart control-plane    # Control plane only
docker compose restart litellm-proxy    # LiteLLM proxy only

# Run tests
uv run pytest

# Format and lint
uv run ruff format
uv run ruff check --fix

# Type check
uv run pyright
```

## Configuration

Copy `.env.example` to `.env` and add your API keys.

- Policies: the control-plane loads the active policy from the YAML file specified by `LUTHIEN_POLICY_CONFIG` (defaults to `config/luthien_config.yaml`).
  - Example: `export LUTHIEN_POLICY_CONFIG=./config/luthien_config.yaml`
  - Minimal YAML:
    ```yaml
    policy: "luthien_proxy.policies.noop:NoOpPolicy"
    # optional
    policy_options:
      stream:
        log_every_n: 1
    ```

## Dev Tooling

- Lint/format: `uv run ruff check` and `uv run ruff format`. Core rules enabled (E/F/I); docstring rules (Google) are introduced gradually.
- Type checking: `uv run pyright` (configured in `[tool.pyright]` within `pyproject.toml`).
- Tests: `uv run pytest -q` with coverage for `src/luthien_proxy/**` configured in `[tool.pytest.ini_options]`.
- Config consolidation: Ruff, Pytest, and Pyright live in `pyproject.toml` to avoid extra files.
- Plan: see `dev/maintainability_plan.md` for the staged rollout (typing, docs, tests, complexity).

## Architecture

- **LiteLLM Proxy**: OpenAI-compatible gateway with custom hooks
- **Control Plane**: Policy orchestration and decision logic
- **Policy Engine**: Configuration and threshold management
- **Debug UI**: `/debug` for recent debug types, `/hooks/trace` for call traces

## Endpoints

- Control Plane (http://localhost:8081):
  - `GET /health` — basic health check
  - `GET /debug` — debug browser
  - `GET /debug/{debug_type}` — view entries for a type
  - `GET /hooks/trace` — UI to trace a call by `call_id`
  - `GET /api/debug/types` — list debug types with counts
  - `GET /api/debug/{debug_type}` — recent entries (default limit 50)
  - `GET /api/debug/{debug_type}/page?page=1&page_size=20` — paginated
  - `GET /api/hooks/recent_call_ids` — recent call IDs
  - `GET /api/hooks/trace_by_call_id?call_id=...` — ordered hook trace

## Control Policies

We keep the policy surface aligned with LiteLLM's hook API, while keeping the proxy thin. Hooks are forwarded “as-is” to the Control Plane; streaming deltas are assembled centrally for policies to consult.

Key files:
- `config/litellm_callback.py`: minimal LiteLLM callback that forwards hook payloads to the control plane.
- `src/luthien_proxy/control_plane/app.py`: FastAPI app with generic hook ingestion, tests, and debug/trace endpoints.
- `src/luthien_proxy/control_plane/stream_context.py`: Redis-backed StreamContextStore for per-call streaming context.
- `src/luthien_proxy/policies/base.py`: abstract policy class including streaming helpers.
- `src/luthien_proxy/policies/noop.py`: default no-op policy.
- `src/luthien_proxy/policies/all_caps.py`: simple example policy.

Note: Local LLM service files (e.g., Dockerfile.local-llm and local_llm_config.yaml) are present for future policies but are not used by current example policies.

Notes:
- Redis is required for streaming context; control-plane startup fails fast if `REDIS_URL` is not set/available.

### Verify

- Start the stack: `./scripts/quick_start.sh`.
- Run the smoke test: `uv run python scripts/test_proxy.py`.
- Tail control-plane logs to see policy actions.

## Troubleshooting

## License

Apache License 2.0
