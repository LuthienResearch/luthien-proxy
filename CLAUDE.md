# Repository Guidelines

## Purpose & Scope

- Core goal: implement AI Control for LLMs with integrated gateway architecture.
- Architecture: FastAPI gateway with integrated control plane and LiteLLM, using event-driven policies.
- **Read `ARCHITECTURE.md` first** when you need to understand how modules connect, how requests flow, or where to make changes. It covers the request lifecycle, module map, key abstractions, and data model.
- Select a policy via `POLICY_CONFIG` that points to a YAML file (defaults to `config/policy_config.yaml`).
  - Example: `export POLICY_CONFIG=./config/policy_config.yaml`

## Sibling Repos

- **luthien-org** (`~/build/luthien-org`): Org-level repo for feedback synthesis, requirements, planning docs, and UI mockups. Lives at `LuthienResearch/luthien-org` on GitHub.
- Cross-repo work (e.g. updating feedback synthesis docs alongside a README PR) should be tracked in the luthien-proxy PR description, not as separate PRs in luthien-org. Commit directly to luthien-org main and link from the luthien-proxy PR.
- Key paths: `ui-fb-dev/1-feedback-synthesis/` (user interview takeaways, value-prop feedback), `ui-fb-dev/2-requirements/` (live requirements docs).
- **Design system:** `ui-fb-dev/design-system.md` — tone, copy constraints, visual specs, trust signals. Load this when editing any user-facing page (landing page, README, UI).

## Development Workflow

1. You will be given an OBJECTIVE to complete (e.g. 'implement this feature', 'fix this bug', 'build this UX', 'refactor this module')
2. Claim the corresponding ticket on the [Trello board](https://trello.com/b/ehoxykPf/luthien?filter=label:luthien-proxy%20TODO) (assign yourself, move to In Progress)
3. Make sure we're on a feature branch (not `main`)
4. Commit the changes, push to origin, and open a draft PR (to `main`)
5. Implement the OBJECTIVE. Add any items that should be done but are out of scope for the current OBJECTIVE to the [Trello board](https://trello.com/b/ehoxykPf/luthien?filter=label:luthien-proxy%20TODO) (e.g. noticing an implementation bug, incorrect documentation, or code that should be refactored).
6. Regularly run `scripts/dev_checks.sh`, then commit and push any formatting/lint fixes along with your changes to origin (on the feature branch).
7. When the OBJECTIVE is complete, add a changelog fragment to `changelog.d/` (see `changelog.d/README.md`)
8. Clear `dev/OBJECTIVE.md` and `dev/NOTES.md`
9. Mark the PR as ready.
10. Mark the Trello ticket as done.

### One PR = One Concern

- **Bug fix discovered while building a feature?** Separate PR.
- **Infrastructure change needed for a feature?** Separate PR, feature depends on it.
- **Ask:** "Could these be reviewed/merged independently?" If yes, split them.

This keeps PRs focused, easier to review, and allows independent merging. **Bug fixes bundled into feature PRs bypass the COE process** — no root cause analysis, no "what else could break" sweep. Always split bug fixes into their own PR so they get a COE. (Example: PR #133 bundled a probe-filtering fix into a feature, skipping COE → same class of bug recurred 38 days later in PR #277.)

### Maintaining Context

Proactively update files in `dev/context/` as you learn about the codebase:

- `codebase_learnings.md`: When you discover architectural patterns, module relationships, or how subsystems work together
- `decisions.md`: When a technical decision is made (e.g., "why we use X instead of Y", "why this API is structured this way")
- `gotchas.md`: When you encounter non-obvious behavior, edge cases, or common mistakes

These files persist across sessions and help build institutional knowledge. Update them during development, not just at the end. Include timestamps (YYYY-MM-DD) when adding entries to detect when knowledge may be stale.

**Reliability warning**: Files in `dev/context/` are written by both humans and AI agents. Agent-written content may contain inferences presented as facts. Before relying on a claim in these docs (especially around how authentication, streaming, or edge cases work), verify against the actual code. If you discover incorrect information, fix it immediately rather than working around it.

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

   - Format everything with `"$(git rev-parse --show-toplevel)/scripts/format_all.sh"`.
   - Full lint + tests + type check: `"$(git rev-parse --show-toplevel)/scripts/dev_checks.sh"`.
   - Quick unit pass: `uv run pytest tests/luthien_proxy/unit_tests`.
   - *infrequently* run full e2e tests using `uv run pytest -m e2e`. This is SLOW and should be used sparingly to validate that core functionality remains intact.
   - **Prefer targeted e2e tests**: Run specific test files first to identify issues before running the full suite: `uv run pytest tests/luthien_proxy/e2e_tests/test_specific_file.py -v`
   - Commit in small chunks with clear messages.

3. **Wrap up the objective**

   ```bash
   "$(git rev-parse --show-toplevel)/scripts/dev_checks.sh"
   git status
   ```

   - Add a changelog fragment to `changelog.d/<short-handle>.md` (see `changelog.d/README.md` for format).
   - Clear `dev/NOTES.md` and `dev/OBJECTIVE.md`.

   ```bash
   git commit -a -m "<objective> is ready"
   gh pr ready
   ```

## Project Structure & Module Organization

- `dev/`: Tracking current development information
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
- `src/luthien_cli/`: Standalone CLI (`pipx install luthien-cli`); `luthien onboard` auto-downloads proxy artifacts
  - `commands/`: Click commands — `onboard`, `claude`, `status`, `up`/`down`, `logs`, `config`
  - `repo.py`: Manages `~/.luthien/luthien-proxy/` — downloads and updates proxy artifacts from GitHub
- `config/`: `policy_config.yaml`
- `scripts/`: developer helpers (`start_gateway.sh` for dockerless dev, `quick_start.sh` for Docker Compose, `quick_start_standalone.sh` for single-container)
- `docker/` + `docker-compose.yaml`: multi-container stack for production deployments (db, redis, gateway)
- `migrations/`: SQL database migrations (auto-applied for SQLite; run by a separate Docker service for Postgres deployments)
- `tests/`: contains test suite organized by package
  - `tests/luthien_proxy/`: Tests for the luthien_proxy package
    - `unit_tests/`: Fast, focused unit tests
    - `integration_tests/`: Integration tests against gateway endpoints
    - `e2e_tests/`: End-to-end tests (slow, run sparingly)
    - `fixtures/`: Shared test fixtures and utilities

## Build, Test, and Development Commands

- Install dev deps: `uv sync --dev`
- Start gateway (dockerless): `./scripts/start_gateway.sh` — runs the gateway as a local Python process with SQLite, no Docker/Postgres/Redis needed
- Run tests: `uv run pytest` (coverage: `uv run pytest --cov=src -q`)
- Lint/format: `uv run ruff format` then `uv run ruff check --fix`. The `scripts/dev_checks.sh` script applies formatting automatically, and VS Code formats on save via Ruff. See `scripts/format_all.sh` for a quick all-in-one solution.
- Type check: `uv run pyright`

### Deployment Modes

**Dockerless (default for development and single-user local use)**:
- Run the gateway directly: `./scripts/start_gateway.sh` or `uv run python -m luthien_proxy.main`
- Uses SQLite (`DATABASE_URL` unset, defaults to `~/.luthien/local.db`) — no Postgres or Redis needed
- Code changes take effect immediately on restart (no image rebuilds)
- The `luthien` CLI defaults to this mode (`luthien onboard` → `luthien up`)

**Single Docker container (self-contained local testing)**:
- `./scripts/quick_start_standalone.sh` — builds one container with everything bundled
- Good for testing the deployment artifact without multi-container orchestration
- Still uses SQLite internally, no Postgres/Redis containers

**Docker Compose with Postgres + Redis (multi-user production)**:
- `./scripts/quick_start.sh` — spins up db, redis, and gateway containers
- Use this for shared/multi-user deployments that need durable storage and pub/sub
- The `docker-compose.yaml` mounts source code as read-only volumes (`./src:/app/src:ro`), so Python changes require `docker compose restart gateway`

Most near-term development work is dockerless. Prefer `start_gateway.sh` for day-to-day work.

## Tooling

- Inspect the dev database with `uv run python scripts/query_debug_logs.py`. The helper loads `.env`, connects to `DATABASE_URL` (works with both SQLite and Postgres), and prints recent conversation events and debug logs.

## Coding Style & Naming Conventions

- Python 3.13; annotate public APIs and important internals. Pyright is the single static checker.
- Formatting via Ruff: double quotes, spaces for indent (see `pyproject.toml`).
- Naming: modules `snake_case`, classes `PascalCase`, functions and vars `snake_case`.
- Docstrings: Google style for public modules/classes/functions; focus on WHY and non-trivial behavior.
- String formatting: prefer f-strings over `.format()` or `%` formatting for readability and performance

## Testing Guidelines

- Framework: `pytest`
- Location: under `tests/luthien_proxy/unit_tests/`, `tests/luthien_proxy/integration_tests/`, `tests/luthien_proxy/e2e_tests/`
- Name files `test_*.py` and mirror package paths within the test-type directory.
- Prefer fast unit tests for policies; add integration tests against gateway endpoints.
- Use `pytest-cov` for coverage; include edge cases for streaming chunk logic.

### Test Requirements

**IMPORTANT: Always write unit tests when adding or significantly modifying code.**

- New modules MUST have corresponding test files in `tests/luthien_proxy/unit_tests/` mirroring the source structure
- New functions/classes MUST have unit tests covering:
  - Happy path behavior
  - Edge cases and error conditions
  - Any format conversions or data transformations
- Refactored code MUST maintain or improve test coverage
- PRs without tests for new functionality will be considered incomplete

## Security & Configuration

- Keep lint, test, and type-check settings consolidated in `pyproject.toml`; avoid extra config files unless necessary.
- Copy `.env.example` to `.env`; never commit secrets.
- Key env vars: `DATABASE_URL`, `POLICY_CONFIG`, `PROXY_API_KEY`. (`REDIS_URL` only needed for Docker Compose deployments.)
- For dockerless dev, use `DATABASE_URL` unset, defaults to `~/.luthien/local.db` (no Postgres needed).
- Update `config/policy_config.yaml` rather than hardcoding.
- Validate setup with test requests to the gateway at `http://localhost:8000`.

## Policy Architecture

Policy instances are **singletons created once at startup** and shared across all concurrent requests. They must be stateless — no request-scoped data on the policy object.

- **Request-scoped state** belongs on `PolicyContext` (via `get_request_state()`) or on the IO object (`AnthropicPolicyIOProtocol`).
- **`PolicyContext`** is created per-request and flows through the entire request/response lifecycle. Use `get_request_state(owner, type, factory)` for typed per-policy state.
- **`AnthropicPolicyIOProtocol`** is the request-scoped I/O surface for Anthropic execution policies — it holds the mutable request, backend response, and streaming methods.
- **`freeze_configured_state()`** runs at policy load time and rejects mutable container attributes on the policy instance. Config-time collections should be immutable (tuple, frozenset).

## Policy Selection

- Policies are loaded from the YAML file pointed to by `POLICY_CONFIG` (default `config/policy_config.yaml`).
- Minimal YAML:

  ```yaml
  policy:
    class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
    config: {}
  ```
