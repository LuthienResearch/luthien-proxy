# Repository Guidelines

## Purpose & Scope
- Core goal: implement AI Control for LLMs on top of the LiteLLM proxy.
- Pattern: Redwood-style control with a centralized control plane making policy decisions; the proxy stays thin.
- Mechanism: a LiteLLM custom logger forwards hook calls to the control plane (`/hooks/pre`, `/hooks/post_success`, `/hooks/stream_chunk`, `/hooks/stream_replacement`). Policies decide allow/rewrite/replace and per-chunk pass/suppress/edit/replace.
- Configure models in `config/litellm_config.yaml`; select a policy via `LUTHIEN_POLICY` (e.g., `export LUTHIEN_POLICY=luthien_control.policies.noop:NoOpPolicy`).

## Project Structure & Module Organization
- `src/luthien_control/`: core package
  - `control_plane/`: FastAPI app and `__main__` launcher
  - `proxy/`: LiteLLM proxy integration and custom logger
  - `policies/`: policy interfaces and defaults (`noop.py`)
  - `monitors/`: trusted/untrusted monitors
- `config/`: `litellm_config.yaml`, `policy_*.yaml`
- `scripts/`: developer helpers (`quick_start.sh`, `test_proxy.py`)
- `docker/` + `docker-compose.yaml`: local stack (db, redis, control-plane, proxy)
- `migrations/`, `prisma/`: database setup
- `tests/`: put unit/integration tests here

## Build, Test, and Development Commands
- Install dev deps: `uv sync --dev`
- Start full stack: `./scripts/quick_start.sh`
- Run tests: `uv run pytest` (coverage: `uv run pytest --cov=src -q`)
- Lint/format: `uv run ruff check --fix` and `uv run ruff format`
- Run control plane locally: `uv run python -m luthien_control.control_plane`
- Run proxy locally: `uv run python -m luthien_control.proxy`
- Docker iterate: `docker compose restart control-plane` or `litellm-proxy`

## Coding Style & Naming Conventions
- Python 3.13; use type hints and `beartype` where applicable.
- Formatting via Ruff: double quotes, spaces for indent (see `pyproject.toml`).
- Names: modules `snake_case`, classes `PascalCase`, functions/vars `snake_case`.
- Keep policies small and testable; avoid side effects in hooks.

## Testing Guidelines
- Framework: `pytest` (+ `pytest-asyncio` for async paths).
- Location: under `tests/`; name files `test_*.py` and mirror package paths.
- Prefer fast unit tests for policies; add integration tests against `/hooks/*` and `/health`.
- Use `pytest-cov` for coverage; include edge cases for streaming chunk logic.

## Commit & Pull Request Guidelines
- Current history is informal; prefer Conventional Commits going forward.
  - Example: `feat(proxy): add stream replacement hook`.
- Commits: small, focused, with rationale in the body when needed.
- PRs: include description, linked issues, screenshots/log snippets, and local test results.
- CI gate: ensure `pre-commit run -a`, `ruff`, and tests pass.

## Security & Configuration Tips
- Copy `.env.example` to `.env`; never commit secrets.
- Key env vars: `DATABASE_URL`, `REDIS_URL`, `CONTROL_PLANE_URL`, `LITELLM_*`, `LUTHIEN_POLICY`.
- Update `config/litellm_config.yaml` and policy YAMLs rather than hardcoding.
- Validate setup with `uv run python scripts/test_proxy.py` and `docker compose logs -f`.
