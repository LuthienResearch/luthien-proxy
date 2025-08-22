# Luthien Control

Redwood-style AI Control as an LLM proxy for production agentic deployments.

## Overview

Luthien Control implements AI safety protocols inspired by Redwood Research's work on AI control, providing:

- **Trusted Supervision**: Monitor and edit LLM outputs using trusted (weaker but aligned) models
- **Untrusted Supervision**: Use the untrusted model itself for monitoring, with honeypots and trusted paraphrasing
- **Defer-to-Resample**: Resample suspicious outputs and defer to trusted models when needed

Built on top of LiteLLM for broad provider compatibility, with a focus on production deployment.

## Features

- Drop-in OpenAI-compatible proxy (via LiteLLM)
- Support for 100+ LLM providers
- Self-hostable with local model support
- Request/response modification (streaming and non-streaming)
- Global stateful policies with PostgreSQL backend
- Real-time alerting and monitoring
- Web UI for policy management

## Quick Start

### Docker Development Environment (Recommended)

The fastest way to get started is with our containerized development environment:

```bash
# One-command setup
./scripts/dev_setup.sh

# Test the setup
uv run python scripts/test_proxy.py

# Monitor services
./scripts/monitor_services.sh
```

This gives you:
- LiteLLM proxy with AI control at http://localhost:4000
- Control plane API at http://localhost:8081
- PostgreSQL database with schema
- Redis for caching
- Ollama for local trusted models

See [scripts/quick_start.md](scripts/quick_start.md) for detailed setup instructions.

### Manual Development Setup

```bash
# Install dependencies
uv sync --dev

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Run individual services (requires external DB/Redis)
uv run python -m luthien_control.proxy
uv run python -m luthien_control.control_plane
```

## Development Commands

```bash
# Run tests
uv run pytest

# Format code
uv run ruff format

# Lint and auto-fix
uv run ruff check --fix

# Run pre-commit hooks
uv run pre-commit run --all-files
```

## Architecture

The system consists of:

1. **LiteLLM Proxy**: OpenAI-compatible gateway with custom hooks
2. **Control Plane**: Policy orchestration and decision logic
3. **Monitors**: Trusted and untrusted model supervision
4. **Policy Engine**: Configuration and threshold management
5. **UI**: Web interface for monitoring and configuration

## License

Apache License 2.0
