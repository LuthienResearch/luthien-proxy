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

- Docker Desktop (or Docker Engine + Compose)
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
```

## Configuration

Copy `.env.example` to `.env` and add your API keys.

## Architecture

- **LiteLLM Proxy**: OpenAI-compatible gateway with custom hooks
- **Control Plane**: Policy orchestration and decision logic
- **Monitors**: Trusted and untrusted model supervision
- **Policy Engine**: Configuration and threshold management

## Control Policies

We keep the policy surface aligned with LiteLLM's hook API, while keeping the proxy thin. Hooks are forwarded “as-is” to the Control Plane; streaming deltas are assembled centrally for policies to consult.

Key files:
- `config/litellm_callback.py`: minimal LiteLLM callback that forwards hook payloads to the control plane.
- `src/luthien_control/control_plane/app.py`: FastAPI app with generic hook ingestion, tests, and debug/trace endpoints.
- `src/luthien_control/control_plane/stream_context.py`: Redis-backed StreamContextStore for per-call streaming context.
- `src/luthien_control/policies/base.py`: abstract policy class including streaming helpers.
- `src/luthien_control/policies/noop.py`: default no-op policy implementation.

Notes:
- Redis is required for streaming context; control-plane startup fails fast if `REDIS_URL` is not set/available.

### Verify

- Start the stack: `./scripts/quick_start.sh`.
- Run the smoke test: `uv run python scripts/test_proxy.py`.
- Tail control-plane logs to see policy actions.

## Troubleshooting

## License

Apache License 2.0
