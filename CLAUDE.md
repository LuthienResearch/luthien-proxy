# Repository Guidelines

## Purpose & Scope

- Core goal: implement AI Control for LLMs on top of the LiteLLM proxy.
- Pattern: Redwood-style control with a centralized control plane making policy decisions; the proxy stays thin.
- Configure models in `config/litellm_config.yaml`.
- Select a policy via `LUTHIEN_POLICY_CONFIG` that points to a YAML file (defaults to `config/luthien_config.yaml`).
  - Example: `export LUTHIEN_POLICY_CONFIG=./config/luthien_config.yaml`

## Development Workflow

1. You will be given an OBJECTIVE to complete (e.g. 'implement this feature', 'fix this bug', 'build this UX', 'refactor this module')
2. Make sure we're on a feature branch (not `main`)
3. Commit the changes, push to origin, and open a draft PR (to `main`)
4. Implement the OBJECTIVE. Add any items that should be done but are out of scope for the current OBJECTIVE to `dev/TODO.md` (e.g. noticing an implementation bug, incorrect documentation, or code that should be refactored).
5. Regularly format + test (`scripts/dev_checks.sh`), commit, and push to origin (on the feature branch).
6. When the OBJECTIVE is complete, update `CHANGELOG.md`
7. Clear `dev/OBJECTIVE.md` and `dev/NOTES.md`
8. Mark the PR as ready.

### Objective Workflow

1. **Start a new objective**

   ```bash
   git checkout main
   git pull
   git checkout -b <short-handle>
   ```

   - If the branch already exists, run `git checkout <short-handle>` instead of creating it.
   - Update `dev/OBJECTIVE.md`, then:

     ```bash
     git add dev/OBJECTIVE.md
     git commit -m "chore: set objective to <short description>"
     git push -u origin objective/<short-handle>
     gh pr create --draft --fill --title "<Objective Title>"
     ```

2. **Develop**

   - Format everything with `./scripts/format_all.sh`.
   - Full lint + tests + type check: `./scripts/dev_checks.sh`.
   - Quick unit pass: `uv run pytest tests/unit_tests`.
   - *infrequenty* run full e2e tests using `uv run pytest -m e2e`. This is SLOW and should be used sparingly to validate that core functionality remains intact.
   - Commit in small chunks with clear messages.

3. **Wrap up the objective**

   ```bash
   ./scripts/dev_checks.sh
   git status
   ```

   - Update `CHANGELOG.md` with a bullet referencing the objective handle.
   - Clear `dev/NOTES.md` and `dev/OBJECTIVE.md`.

   ```bash
   git commit -a -m "<objective> is ready"
   gh pr ready
   ```

## Project Structure & Module Organization

- `dev/`: Tracking current development information
  - `TODO.md`: TODO list. Add to this when we notice changes we should make that are out-of-scope for the current PR.
  - `OBJECTIVE.md`: Succinct statement of the active objective with acceptance check.
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
- Lint/format: `uv run ruff format` then `uv run ruff check --fix`. The `scripts/dev_checks.sh` script applies formatting automatically, and VS Code formats on save via Ruff. See `scripts/format_all.sh` for a quick all-in-one solution.
- Lint/format: `uv run ruff format` then `uv run ruff check --fix`; `./scripts/dev_checks.sh` wraps both plus pytest and pyright.
- Type check: `uv run pyright`
- Run control plane locally: `uv run python -m luthien_proxy.control_plane`
- Run proxy locally: `uv run python -m luthien_proxy.proxy`
- Docker iterate: `docker compose restart control-plane` or `litellm-proxy`

## Coding Style & Naming Conventions

- Python 3.13; annotate public APIs and important internals. Pyright is the single static checker.
- Formatting via Ruff: double quotes, spaces for indent (see `pyproject.toml`).
- Naming: modules `snake_case`, classes `PascalCase`, functions and vars `snake_case`.
- Docstrings: Google style for public modules/classes/functions; focus on WHY and non-trivial behavior.
- Optional runtime type checking (`beartype`) for critical sections
- String formatting: prefer f-strings over `.format()` or `%` formatting for readability and performance

## Testing Guidelines

- Framework: `pytest`
- Location: under `tests/unit_tests/`, `tests/integration_tests/`, `tests/e2e_tests/`
- Name files `test_*.py` and mirror package paths within the test-type directory.
- Prefer fast unit tests for policies; add integration tests against `/api/hooks/*` and `/health`.
- Use `pytest-cov` for coverage; include edge cases for streaming chunk logic.

## Security & Configuration

- Keep lint, test, and type-check settings consolidated in `pyproject.toml`; avoid extra config files unless necessary.
- Copy `.env.example` to `.env`; never commit secrets.
- Key env vars: `DATABASE_URL`, `REDIS_URL`, `CONTROL_PLANE_URL`, `LITELLM_*`, `LUTHIEN_POLICY_CONFIG`.
- Update `config/litellm_config.yaml` and `config/luthien_config.yaml` rather than hardcoding.
- Validate setup with `uv run python scripts/test_proxy.py` and `docker compose logs -f`.

## Policy Selection

- Policies are loaded from the YAML file pointed to by `LUTHIEN_POLICY_CONFIG` (default `config/luthien_config.yaml`).
- Minimal YAML:

  ```yaml
  policy:
    class: "luthien_proxy.policies.noop:NoOpPolicy"
    config:
      policy_specific_arg: 'someval'
  ```
