# Repository Guidelines

## Purpose & Scope

- Core goal: implement AI Control for LLMs on top of the LiteLLM proxy.
- Pattern: Redwood-style control with a centralized control plane making policy decisions; the proxy stays thin.
- Configure models in `config/litellm_config.yaml`.
- Select a policy via `LUTHIEN_POLICY_CONFIG` that points to a YAML file (defaults to `config/luthien_config.yaml`).
  - Example: `export LUTHIEN_POLICY_CONFIG=./config/luthien_config.yaml`

## Project Structure & Module Organization

- `src/luthien_proxy/`: core package
  - `control_plane/`: FastAPI app and `__main__` launcher
  - `proxy/`: LiteLLM proxy integration and custom logger
  - `policies/`: policy interfaces and defaults (`noop.py`)
  - `control_plane/templates` + `static`: debug and trace UIs
- `config/`: `litellm_config.yaml`, `luthien_config.yaml`
- `scripts/`: developer helpers (`quick_start.sh`, `test_proxy.py`)
- `docker/` + `docker-compose.yaml`: local stack (db, redis, control-plane, proxy)
- `migrations/`, `prisma/`: database setup
- `tests/`: put unit/integration tests here

## Build, Test, and Development Commands

- Install dev deps: `uv sync --dev`
- Start full stack: `./scripts/quick_start.sh`
- Run tests: `uv run pytest` (coverage: `uv run pytest --cov=src -q`)
- Lint/format: `uv run ruff check --fix` and `uv run ruff format`
- Type check: `uv run pyright`
- Run control plane locally: `uv run python -m luthien_proxy.control_plane`
- Run proxy locally: `uv run python -m luthien_proxy.proxy`
- Docker iterate: `docker compose restart control-plane` or `litellm-proxy`

## Coding Style & Naming Conventions

- Python 3.13; annotate public APIs and important internals. Pyright is the single static checker.
- Formatting via Ruff: double quotes, spaces for indent (see `pyproject.toml`).
- Names: modules `snake_case`, classes `PascalCase`, functions/vars `snake_case`.
- Keep policies small and testable; avoid side effects in hooks.
- Docstrings: Google style for public modules/classes/functions; focus on WHY and non-trivial behavior.
- Runtime type checking (`beartype`) is optional and only for critical boundaries during dev/tests.

## Testing Guidelines

- Framework: `pytest` (+ `pytest-asyncio` for async paths).
- Location: under `tests/`; name files `test_*.py` and mirror package paths.
- Prefer fast unit tests for policies; add integration tests against `/hooks/*` and `/health`.
- Use `pytest-cov` for coverage; include edge cases for streaming chunk logic.

## Tooling & Config

- Consolidate config in `pyproject.toml`:
  - Ruff lint/format, Pytest options, Pyright settings.
- Avoid extra config files unless needed; if Pyright cannot read `pyproject.toml` in your environment, fall back to a single `pyrightconfig.json`.
- A staged maintainability plan lives in `dev/maintainability_plan.md`.

## Commit & Pull Request Guidelines

- Current history is informal; prefer Conventional Commits going forward.
  - Example: `feat(proxy): add stream replacement hook`.
- Commits: small, focused, with rationale in the body when needed.
- PRs: include description, linked issues, screenshots/log snippets, and local test results.
- CI gate: ensure `pre-commit run -a`, `ruff`, and tests pass.

## Security & Configuration Tips

- Copy `.env.example` to `.env`; never commit secrets.
- Key env vars: `DATABASE_URL`, `REDIS_URL`, `CONTROL_PLANE_URL`, `LITELLM_*`, `LUTHIEN_POLICY_CONFIG`.
- Update `config/litellm_config.yaml` and `config/luthien_config.yaml` rather than hardcoding.
- Validate setup with `uv run python scripts/test_proxy.py` and `docker compose logs -f`.

## Policy Selection

- Policies are loaded from the YAML file pointed to by `LUTHIEN_POLICY_CONFIG` (default `config/luthien_config.yaml`).
- Minimal YAML:

  ```yaml
  policy: "luthien_proxy.policies.noop:NoOpPolicy"
  # optional
  policy_options:
    stream:
      log_every_n: 1
  ```
