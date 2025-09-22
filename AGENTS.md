# Repository Guidelines

## Purpose & Scope

- Core goal: implement AI Control for LLMs on top of the LiteLLM proxy.
- Pattern: Redwood-style control with a centralized control plane making policy decisions; the proxy stays thin.
- Configure models in `config/litellm_config.yaml`.
- Select a policy via `LUTHIEN_POLICY_CONFIG` that points to a YAML file (defaults to `config/luthien_config.yaml`).
  - Example: `export LUTHIEN_POLICY_CONFIG=./config/luthien_config.yaml`

## Development Strategy

- At any given time, we have one OBJECTIVE, listed in `dev/OBJECTIVE.md`. Keep it to a single sentence that captures the concrete user-facing change we are pursuing right now.
- Opening a new objective:
  - Create a feature branch named `objective/<short-handle>` (e.g., `objective/policy-engine-cleanup`).
  - Update `dev/OBJECTIVE.md` with the new objective, owner, start date, and acceptance check.
  - Open a draft PR targeting `main` to give reviewers visibility.
- While delivering an objective:
  - Work only happens on that feature branch; stack commits as needed.
  - Add any running design thoughts to `dev/NOTES.md` while work is in flight.
  - Capture out-of-scope discoveries in `dev/TODO.md` so the objective stays tight.
- Closing an objective:
  - Ensure `./scripts/dev_checks.sh` passes (ruff format/check, pytest, pyright) before marking the PR ready for review.
  - Update `CHANGELOG.md` with a bullet that links back to the objective ID or branch handle.
  - Clear `dev/NOTES.md` and reset `dev/OBJECTIVE.md` so the next objective starts fresh.
  - Mark the PR ready.

## Project Structure & Module Organization

- `dev/`: Tracking current development information
  - `TODO.md`: TODO list. Add to this when we notice changes we should make that are out-of-scope for the current PR.
  - `OBJECTIVE.md`: Succinct statement of the active objective with owner, start date, and acceptance check.
  - `NOTES.md`: Scratchpad for implementation details while the current objective is in progress.
  - `*plan.md`: Medium- and long-term development plans may be recorded here.
- `CHANGELOG.md`: Record changes as we make them (typically updated when we complete an OBJECTIVE)
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
- Lint/format: `uv run ruff format` then `uv run ruff check --fix`. The `scripts/dev_checks.sh` script applies formatting automatically, and VS Code formats on save via Ruff.
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
