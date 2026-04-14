# Repository Guidelines

## Purpose & Scope

- Core goal: implement AI Control for LLMs with integrated gateway architecture.
- Architecture: FastAPI gateway with integrated control plane, using event-driven policies. LiteLLM handles judge/LLM policy calls.
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
   - E2E tests: `./scripts/run_e2e.sh` — runs all tiers, stops on first failure, resumes on re-run.
   - E2E specific tier: `./scripts/run_e2e.sh sqlite` or `./scripts/run_e2e.sh mock`
   - E2E fresh start: `./scripts/run_e2e.sh --fresh`
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

- `dev-README.md`: **Development guide** — dev commands, releasing, architecture, endpoints, policy system
- `dev/`: Tracking current development information
  - `OBJECTIVE.md`: Succinct statement of the active objective with acceptance check.
  - `NOTES.md`: Scratchpad for implementation details while the current objective is in progress.
  - `*plan.md`: Medium- and long-term development plans may be recorded here.
  - `context/`: Persistent knowledge accumulated across sessions (check these into git)
    - `codebase_learnings.md`: Patterns, architecture insights, gotchas discovered while working
    - `decisions.md`: Technical decisions made and their rationale
    - `gotchas.md`: Non-obvious behaviors, edge cases, things that are easy to get wrong
- `CHANGELOG.md`: Record changes as we make them (typically updated when we complete an OBJECTIVE)
- `src/luthien_proxy/`: core gateway package
  - `main.py`: FastAPI app factory, lifespan wiring, CLI entry point
  - `gateway_routes.py`: `/v1/messages` and related Anthropic-shaped proxy endpoints
  - `auth.py`: bearer/API-key/session auth helpers for admin and debug endpoints
  - `session.py`: cookie-based browser login flow for the admin UI (companion to `auth.py`)
  - `dependencies.py`: FastAPI dependency providers (policy, emitter, credential store, etc.)
  - `config.py`: policy YAML loading (`load_policy_from_yaml`) and policy class import (separate from the gateway config system below)
  - `config_fields.py`, `config_registry.py`, `settings.py`: gateway config system (see "Configuration System" below). `settings.py` is auto-generated — do not edit by hand.
  - `policy_manager.py`: loads the active policy from YAML or DB and hot-swaps at runtime
  - `policy_composition.py`: `compose_policy()` helper for wrapping policies in a `MultiSerialPolicy` chain
  - `credential_manager.py`: validates client credentials (client_key / passthrough / both) and caches results
  - `telemetry.py`: OpenTelemetry tracing setup (distinct from `observability/`, which handles event emission)
  - `pipeline/`: request processing pipeline — request entry point, client format detection, policy-context injection, stream protocol validation
  - `policies/`: concrete policy implementations; `policies/presets/` holds reusable rule presets (e.g. `block_dangerous_commands`, `no_yapping`)
  - `policy_core/`: policy contract layer — `BasePolicy`, `AnthropicExecutionInterface`, `AnthropicHookPolicy`, `PolicyContext`, `TextModifierPolicy`
  - `credentials/`: typed credential models (`Credential`, `CredentialType`) and per-policy `AuthProvider` strategies (`UserCredentials`, `ServerKey`, `UserThenServer`), plus the DB-backed credential store
  - `llm/`: Anthropic HTTP client wrapper, per-credential client cache, LiteLLM-based judge client, and shared Anthropic type definitions
  - `observability/`: event emitter, Redis/in-process event publishers, Sentry integration, and the SSE generator (`stream_activity_events`) wired into the activity monitor UI
  - `storage/`: helpers for persisting and reconstructing conversation events
  - `request_log/`: HTTP-level request/response recording for `/v1/` traffic (gated by `ENABLE_REQUEST_LOGGING`)
  - `usage_telemetry/`: anonymous aggregate usage metrics sent to the central telemetry endpoint
  - `admin/`: admin API routes and policy class discovery
  - `debug/`: debug endpoints for inspecting recorded conversation events
  - `history/`: conversation history browsing and export
  - `ui/`: protected HTML routes for the admin UI pages
  - `static/`: HTML/JS/CSS assets served by the UI routes
  - `utils/`: shared infrastructure — DB pools (Postgres + SQLite), Redis client, credential cache, migration check, URL helpers
- `src/luthien_cli/`: Standalone CLI (`uv tool install luthien-cli`); `luthien onboard` auto-downloads proxy artifacts
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

See **[dev-README.md](dev-README.md)** for the canonical reference: dev commands, architecture, endpoints, deployment modes, releasing, and observability.

Quick reference for frequent commands:

- `./scripts/dev_checks.sh` — format + lint + tests + type check (run before pushing)
- `uv run pytest tests/luthien_proxy/unit_tests` — quick unit pass
- `./scripts/run_e2e.sh` — all e2e tiers
- `./scripts/start_gateway.sh` — start gateway locally (dockerless, SQLite)

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

### Existing Test Infrastructure

This repo has extensive test infrastructure — **use it before building manual test setups**. Read the CLAUDE.md in the relevant test directory for full details and examples.

**Run all e2e tests:** `./scripts/run_e2e.sh` (stops on first failure, resumes on re-run, logs to `.e2e-logs/`)

| Tier | Marker | Infra needed | Speed | When to use |
|------|--------|-------------|-------|-------------|
| Unit | *(default)* | None | Fast | Logic, edge cases, mock external calls |
| sqlite_e2e | `sqlite_e2e` | None (in-process) | Medium | Full gateway behavior, no Docker |
| mock_e2e | `mock_e2e` | None (in-process) | Medium | Deterministic streaming, policy behavior |
| e2e | `e2e` | Docker + real API | Slow | Final validation, real-world behavior |

Key helpers in `tests/luthien_proxy/e2e_tests/`:
- `conftest.py`: `policy_context(class_ref, config)` — hot-swap policy via admin API, auto-restore
- `mock_anthropic/simulator.py`: `ClaudeCodeSimulator` — simulate Claude Code client sessions
- `mock_anthropic/server.py`: `MockAnthropicServer` — enqueue deterministic responses

Quick smoke test against a running gateway: `scripts/test_gateway.sh`

Note: `sqlite_e2e` and `mock_e2e` must run in **separate pytest sessions** — `run_e2e.sh` handles this automatically.

### Test Requirements

**IMPORTANT: Always write unit tests when adding or significantly modifying code.**

- New modules MUST have corresponding test files in `tests/luthien_proxy/unit_tests/` mirroring the source structure
- New functions/classes MUST have unit tests covering:
  - Happy path behavior
  - Edge cases and error conditions
  - Any format conversions or data transformations
- Refactored code MUST maintain or improve test coverage
- PRs without tests for new functionality will be considered incomplete

## Migrations

Migrations live in `migrations/postgres/` and `migrations/sqlite/`. Every Postgres migration needs a matching SQLite migration with the same number prefix. See `migrations/CLAUDE.md` for type translation rules and workflow.

## Configuration System

All gateway configuration is defined in `src/luthien_proxy/config_fields.py` — single source of truth for field names, env vars, types, defaults, descriptions, and metadata.

- **Config registry** (`config_registry.py`): resolves values through CLI args > env vars > DB (`gateway_config` table) > defaults, with provenance tracking.
- **Adding a new config value**: add a `ConfigFieldMeta` to `CONFIG_FIELDS` in `config_fields.py`, then run `uv run python scripts/generate_settings.py` (or let `dev_checks.sh` do it). `settings.py` is auto-generated — don't edit it by hand. Regenerate `.env.example` with `uv run python scripts/generate_env_example.py > .env.example`.
- **Config dashboard**: `/config` in the admin UI shows all fields with active values, provenance, and inline editing for DB-settable fields.
- **Admin API**: `GET /api/admin/config` returns all fields; `PUT /api/admin/config/{key}` sets DB-settable values.
- `.env.example` is auto-generated from the config spec — don't edit it by hand.

### Environment Setup

- Copy `.env.example` to `.env`; never commit secrets.
- Key env vars: `DATABASE_URL`, `POLICY_CONFIG`, `ADMIN_API_KEY`. (`REDIS_URL` only needed for Docker Compose deployments.)
  - `CLIENT_API_KEY` and `ANTHROPIC_API_KEY` are optional and only apply in specific auth modes — see [`dev/context/authentication.md`](dev/context/authentication.md).
- Policy env vars:
  - `POLICY_SOURCE` — policy loading strategy: `db`, `file`, `db-fallback-file` (default), or `file-fallback-db`.
  - `POLICY_CONFIG` — path to the policy YAML file (used when `POLICY_SOURCE` resolves to file).
- For dockerless dev, leave `DATABASE_URL` unset — defaults to `~/.luthien/local.db` (no Postgres needed).
- All config values can be overridden via CLI flags: `python -m luthien_proxy.main --gateway-port 9000`.
- Full field list lives in `src/luthien_proxy/config_fields.py`; `.env.example` is auto-generated from it.
- Keep lint, test, and type-check settings consolidated in `pyproject.toml`; avoid extra config files unless necessary.

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


## Authentication Modes

- Gateway auth modes are `client_key`, `passthrough`, or `both`.
- Passthrough validation goes through Anthropic's free `count_tokens` endpoint and is cached.
- Do not hardcode one auth mode in route or policy logic; consult the configured mode.

## Streaming and SSE

- Anthropic SSE ordering is strict; invalid event sequences corrupt clients and history reconstruction.
- `content_block_*` events must complete before `message_delta` / `message_stop`.
- Mid-stream failures must surface as inline SSE `error` events, not pre-header HTTP exceptions.
- Streaming changes need extra scrutiny because `anthropic_processor.py` also feeds observability and history reconstruction.

## Database Abstraction

- The codebase deliberately shares one query path across Postgres and SQLite wherever possible.
- Prefer a single SQL statement plus small dialect branches or SQLite translation shims.
- Do not fork whole query implementations unless the dialect gap is real and localized alternatives failed.
- `utils/db_sqlite.py` translates asyncpg-style placeholders and selected PG syntax; rely on it instead of re-inventing per-call adapters.

## Anti-Patterns (This Project)

- Editing `settings.py` or `.env.example` directly — they are auto-generated.
- Duplicating full Postgres and SQLite query bodies when a shared path would work.
- Suppressing type errors instead of fixing types.
- Putting mutable request data on policy instances.
- Treating streaming changes as "just formatting"; they affect SSE validity, history, and policy execution.
- Bundling missing changelog work into a later PR.

## Subdirectory Guides

- `src/luthien_proxy/pipeline/AGENTS.md` for request lifecycle and SSE work.
- `src/luthien_proxy/policies/AGENTS.md` for policy authoring and composition.
- `src/luthien_proxy/utils/AGENTS.md` for DB/cache/config plumbing.
- `migrations/AGENTS.md` for migration authoring details.
- `saas_infra/AGENTS.md` for Railway deployment infrastructure.
- `src/luthien_cli/AGENTS.md` for the CLI package.
- `tests/luthien_proxy/e2e_tests/AGENTS.md` for e2e test tiers and fixtures.
- `tests/luthien_proxy/unit_tests/AGENTS.md` for unit testing philosophy and rules.
