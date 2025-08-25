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

### Prerequisites

- **Docker Desktop** (or Docker Engine + Docker Compose)
- **Python 3.13+**
- **uv** (Python package manager) - Install from https://docs.astral.sh/uv/

### 2-Step Setup

```bash
# 1. Start everything
./scripts/quick_start.sh

# 2. Test it works
uv run python scripts/test_proxy.py
```

That's it! You now have:
- **LiteLLM Proxy** with AI control at http://localhost:4000
- **Control Plane** API at http://localhost:8081
- **PostgreSQL** database with complete schema (2 databases: luthien_control and litellm_db)
- **Redis** for caching and ephemeral state
- All services health-checked and ready

### First API Call

Test your setup with a simple API call:

```bash
curl -X POST http://localhost:4000/chat/completions \
  -H "Authorization: Bearer sk-luthien-dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello! Write a simple Python hello world script."}],
    "max_tokens": 200
  }'
```

This request will flow through the AI control system, be evaluated by policies, and logged for audit.

### Docker Development Environment

After initial setup, use these commands for daily development:

```bash
# Start services
./scripts/quick_start.sh

# After making code changes, restart specific services:
docker compose restart control-plane    # Control plane only
docker compose restart litellm-proxy    # LiteLLM proxy only
docker compose restart                  # All services
```

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

## Development Guide

### Environment Configuration

All service ports and URLs are configured via environment variables in `.env`:

```bash
# Copy template and customize
cp .env.example .env
# Edit .env to add your API keys for LLM providers
```

Key environment variables:
- `LITELLM_PORT=4000` - LiteLLM proxy port
- `CONTROL_PLANE_PORT=8081` - Control plane API port
- `POSTGRES_PORT=5432` - Database port
- `REDIS_PORT=6379` - Redis cache port

### Available Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `quick_start.sh` | Complete environment setup | `./scripts/quick_start.sh` |
| `test_proxy.py` | End-to-end testing suite | `uv run python scripts/test_proxy.py` |

### Daily Development Workflow

1. **Start development environment:**
   ```bash
   ./scripts/quick_start.sh
   ```

2. **Make code changes** in `src/` directory

3. **Restart affected services:**
   ```bash
   docker compose restart control-plane    # Control plane only
   docker compose restart litellm-proxy    # LiteLLM proxy only
   docker compose restart                  # All services
   ```

4. **Test your changes:**
   ```bash
   uv run python scripts/test_proxy.py
   ```

5. **Check service status:**
   ```bash
   docker compose ps                       # Shows service status
   ```

6. **View logs for debugging:**
   ```bash
   docker compose logs -f                  # All services
   docker compose logs -f litellm-proxy    # Specific service
   ```

### Code Quality

```bash
# Run tests
uv run pytest
uv run pytest tests/unit/test_specific.py::TestClass::test_method  # Specific test

# Format and lint
uv run ruff format           # Format code
uv run ruff check --fix      # Lint with auto-fix
uv run pre-commit run --all-files  # Run all pre-commit hooks
```

### Service Management

```bash
# Start/stop services
docker compose up -d         # Start all services
docker compose down          # Stop all services
docker compose restart control-plane  # Restart specific service

# Health checks
curl http://localhost:4000/test      # LiteLLM proxy health
curl http://localhost:8081/health    # Control plane health

# Database access
docker compose exec db psql -U luthien -d luthien_control
```

### Troubleshooting

**Services won't start:**
```bash
docker compose ps            # Check service status
docker compose logs          # Check error logs
docker compose down && ./scripts/quick_start.sh  # Complete restart
```

**Code changes not reflected:**
```bash
docker compose restart       # Restart all services
docker compose logs -f [service]  # Check for startup errors
```

**Database connection issues:**
```bash
# Reset database completely
docker compose down -v && docker compose up -d
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
