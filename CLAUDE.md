# CLAUDE.md

## Project Overview

Luthien Control implements Redwood Research-style AI Control as a production-ready LLM proxy built on LiteLLM for compatible proxying across 100+ providers.

## Development Commands

### Environment Setup
```bash
# Complete setup - validates dependencies, installs packages, starts services
./scripts/quick_start.sh

# Check service status
docker compose ps

# After code changes, restart services
docker compose restart control-plane    # Control plane only
docker compose restart litellm-proxy    # LiteLLM proxy only
docker compose restart                  # All services
```

### Python Development
```bash
# Install dependencies
uv sync

# Install dev dependencies
uv sync --dev

# Run tests
uv run pytest
uv run pytest tests/unit/test_specific.py::TestClass::test_method  # single test

# Format code
uv run ruff format

# Lint and auto-fix
uv run ruff check --fix

# Run pre-commit hooks manually
uv run pre-commit run --all-files
```

### Manual Service Control
```bash
# Stop all services
docker compose down

# View logs
docker compose logs -f
docker compose logs -f [service_name]  # specific service

# Test endpoints
curl http://localhost:${LITELLM_PORT:-4000}/test         # LiteLLM proxy health
curl http://localhost:${CONTROL_PLANE_PORT:-8081}/health # Control plane health

# Start specific services manually (requires external DB/Redis)
uv run python -m luthien_control.proxy       # LiteLLM proxy with hooks
uv run python -m luthien_control.control_plane  # Control plane service
```

## Architecture

The system consists of five interconnected components:

1. **LiteLLM Proxy** (`src/luthien_control/proxy/`): Implements CustomLogger with async hooks
   - `async_pre_call_hook`: Modify requests, add episode IDs, fetch policies
   - `async_post_call_success_hook`: Score responses, trigger editing/deferral
   - `async_post_call_streaming_iterator_hook`: Monitor/modify streaming responses

2. **Control Plane** (`src/luthien_control/control_plane/`): FastAPI service orchestrating control protocols
   - `/policy/evaluate`: Main decision endpoint for pre/post hooks
   - `/policy/on_chunk`: Streaming chunk evaluation
   - `/resample`: Orchestrate k-resample protocol
   - Manages policy state, budgets, and audit allocation

3. **Monitors** (`src/luthien_control/monitors/`): Scoring and supervision
   - Trusted monitor: Local model scoring (via Ollama/vLLM)
   - Untrusted monitor: Uses main model with honeypot validation
   - Paraphraser: Trusted semantic conversion for collusion prevention

4. **Policies** (`src/luthien_control/policies/`): Configuration management
   - YAML/JSON DSL for thresholds, budgets, protocol settings
   - Per-episode state tracking
   - Honeypot regimen configuration

5. **Data Layer**:
   - PostgreSQL: Persistent state (policies, episodes, decisions, logs)
   - Redis: Ephemeral state (counters, rate limits, stream flags)

## Key Implementation Details

- **Type Safety**: All functions use beartype for runtime type checking
- **Async First**: Core logic is async for high-concurrency proxy operations
- **Fail-Safe**: If untrusted monitor fails honeypots, system falls back to trusted-only mode
- **Streaming**: Buffer-based approach for chunk evaluation with cutover capability
- **Correlation**: Use LiteLLM's `call_id` to correlate proxy requests with control decisions

## Implementation Plan Reference

See `plan.md` for detailed implementation phases:
- Phase 0: LiteLLM setup with basic hooks
- Phase 1: Trusted monitoring with request/response rewrites
- Phase 2: Untrusted monitoring with paraphrasing/honeypots
- Phase 3: Defer-to-resample protocol
- Phase 4: UI and alerting

## Testing Approach

- Unit tests for individual monitors and policy logic
- Integration tests for proxy hooks and control plane interaction
- End-to-end tests simulating complete control protocols with mock LLMs
- Honeypot validation tests to ensure detection thresholds work correctly
- "docker compose", not "docker-compose"
