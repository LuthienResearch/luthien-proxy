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

## License

Apache License 2.0
