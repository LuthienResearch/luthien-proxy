# CLAUDE.md

## Project Overview

Luthien Control: Redwood Research-style AI Control as a production LLM proxy built on LiteLLM.

## Essential Commands

```bash
# Setup & start
./scripts/quick_start.sh

# After code changes
docker compose restart control-plane    # or litellm-proxy

# Run tests & lint
uv run pytest
uv run ruff format
uv run ruff check --fix

# Debug
docker compose logs -f [service_name]
```

## Architecture

1. **LiteLLM Proxy** (`src/luthien_control/proxy/`): CustomLogger with async hooks
   - Pre-call: Modify requests, add episode IDs
   - Post-call: Score responses, trigger control protocols
   - Streaming: Monitor/modify chunks in real-time

2. **Control Plane** (`src/luthien_control/control_plane/`): FastAPI orchestration
   - `/policy/evaluate`: Main decision endpoint
   - `/resample`: K-resample protocol
   - Manages budgets and audit allocation

3. **Monitors** (`src/luthien_control/monitors/`):
   - Trusted: Local model scoring
   - Untrusted: Main model with honeypots
   - Paraphraser: Prevent collusion

4. **Policies** (`src/luthien_control/policies/`): YAML/JSON configuration

5. **Data**: PostgreSQL (persistent) + Redis (ephemeral)

## Code Standards

- Use beartype for type checking
- Async-first for all proxy operations
- Fail-safe: Fall back to trusted-only on honeypot failures
- Correlate via LiteLLM's `call_id`

## Testing

Run full test suite before commits:
```bash
uv run pytest
uv run pre-commit run --all-files
```

## References

- Implementation phases: `plan.md`
- Note: Use "docker compose" not "docker-compose"
