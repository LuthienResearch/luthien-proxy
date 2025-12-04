# Repository Guidelines

## Purpose & Scope

- Core goal: implement AI Control for LLMs with integrated gateway architecture.
- Architecture: FastAPI gateway with integrated control plane and LiteLLM, using event-driven policies.
- Configure upstream LLM models in `config/local_llm_config.yaml`.
- Select a policy via `POLICY_CONFIG` that points to a YAML file (defaults to `config/policy_config.yaml`).
  - Example: `export POLICY_CONFIG=./config/policy_config.yaml`

## Development Workflow

1. You will be given an OBJECTIVE to complete (e.g. 'implement this feature', 'fix this bug', 'build this UX', 'refactor this module')
2. Make sure we're on a feature branch (not `main`)
3. Commit the changes, push to origin, and open a draft PR (to `main`)
4. Implement the OBJECTIVE. Add any items that should be done but are out of scope for the current OBJECTIVE to `dev/TODO.md` (e.g. noticing an implementation bug, incorrect documentation, or code that should be refactored).
5. Regularly format + test (`scripts/dev_checks.sh`), commit, and push to origin (on the feature branch).
6. When the OBJECTIVE is complete, update `CHANGELOG.md`
7. Clear `dev/OBJECTIVE.md` and `dev/NOTES.md`
8. Mark the PR as ready.

### Maintaining Context

Proactively update files in `dev/context/` as you learn about the codebase:

- `codebase_learnings.md`: When you discover architectural patterns, module relationships, or how subsystems work together
- `decisions.md`: When a technical decision is made (e.g., "why we use X instead of Y", "why this API is structured this way")
- `gotchas.md`: When you encounter non-obvious behavior, edge cases, or common mistakes

These files persist across sessions and help build institutional knowledge. Update them during development, not just at the end. Include timestamps (YYYY-MM-DD) when adding entries to detect when knowledge may be stale.

Note that both Claude Code and Codex agents work in this repo and may read from and write to context.

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
  - `context/`: Persistent knowledge accumulated across sessions (check these into git)
    - `codebase_learnings.md`: Patterns, architecture insights, gotchas discovered while working
    - `decisions.md`: Technical decisions made and their rationale
    - `gotchas.md`: Non-obvious behaviors, edge cases, things that are easy to get wrong
- `CHANGELOG.md`: Record changes as we make them (typically updated when we complete an OBJECTIVE)
- `src/luthien_proxy/`: core package
  - `orchestration/`: PolicyOrchestrator coordinates streaming pipeline
  - `policies/`: Event-driven policy implementations
  - `policy_core/`: Policy protocol, contexts, and chunk builders
  - `streaming/`: Policy executor and client formatters
  - `storage/`: Conversation event persistence
  - `observability/`: OpenTelemetry integration, transaction recording
  - `admin/`: Runtime policy management API
  - `debug/`: Debug endpoints for inspecting conversation events
  - `ui/`: Activity monitoring and diff viewer interfaces
  - `llm/`: LiteLLM client wrapper and format converters
  - `utils/`: Shared utilities (db, redis, validation)
- `config/`: `policy_config.yaml`, `local_llm_config.yaml`
- `scripts/`: developer helpers (`quick_start.sh`, `test_gateway.sh`)
- `docker/` + `docker-compose.yaml`: local stack (db, redis, gateway, local-llm)
- `migrations/`, `prisma/`: database setup
- `tests/`: unit/integration tests

## Build, Test, and Development Commands

- Install dev deps: `uv sync --dev`
- Start full stack: `./scripts/quick_start.sh`
- Run tests: `uv run pytest` (coverage: `uv run pytest --cov=src -q`)
- Lint/format: `uv run ruff format` then `uv run ruff check --fix`. The `scripts/dev_checks.sh` script applies formatting automatically, and VS Code formats on save via Ruff. See `scripts/format_all.sh` for a quick all-in-one solution.
- Type check: `uv run pyright`
- Docker iterate: `docker compose restart gateway`

## Tooling

- Inspect the dev Postgres quickly with `uv run python scripts/query_debug_logs.py`. The helper loads `.env`, connects to `DATABASE_URL`, and prints recent conversation events and debug logs.
- Quick Codex feedback: run `codex e "question or idea"` for a fast suggestion without starting an interactive session. Note that subsequent invocations won't persist context - pass in existing context or refer to relevant files to persist some context between calls.

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
- Prefer fast unit tests for policies; add integration tests against gateway endpoints.
- Use `pytest-cov` for coverage; include edge cases for streaming chunk logic.

## Security & Configuration

- Keep lint, test, and type-check settings consolidated in `pyproject.toml`; avoid extra config files unless necessary.
- Copy `.env.example` to `.env`; never commit secrets.
- Key env vars: `DATABASE_URL`, `REDIS_URL`, `POLICY_CONFIG`, `PROXY_API_KEY`.
- Update `config/policy_config.yaml` rather than hardcoding.
- Validate setup with test requests to the gateway at `http://localhost:8000`.

## Policy Selection

- Policies are loaded from the YAML file pointed to by `POLICY_CONFIG` (default `config/policy_config.yaml`).
- Minimal YAML:

  ```yaml
  policy:
    class: "luthien_proxy.policies.event_based_noop:EventBasedNoOpPolicy"
    config: {}
  ```
