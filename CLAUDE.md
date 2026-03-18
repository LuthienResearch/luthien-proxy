# Repository Guidelines

## Purpose & Scope

- Core goal: implement AI Control for LLMs with integrated gateway architecture.
- Architecture: FastAPI gateway with integrated control plane and LiteLLM, using event-driven policies.
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
7. When the OBJECTIVE is complete, update `CHANGELOG.md`
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

Note that Claude Code agents work in this repo and may read from and write to context.

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
   - *infrequently* run full e2e tests using `uv run pytest -m e2e`. This is SLOW and should be used sparingly to validate that core functionality remains intact.
   - **Prefer targeted e2e tests**: Run specific test files first to identify issues before running the full suite: `uv run pytest tests/e2e_tests/test_specific_file.py -v`
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
- `scripts/`: developer helpers (`quick_start.sh`, `test_gateway.sh`)
- `docker/` + `docker-compose.yaml`: local stack (db, redis, gateway)
- `migrations/`: SQL database migrations (run by a separate Docker service before gateway starts; gateway validates migration state on startup via `check_migrations`)
- `tests/`: unit/integration tests

## Build, Test, and Development Commands

- Install dev deps: `uv sync --dev`
- Start full stack: `./scripts/quick_start.sh` (handles auto-port-selection; raw `docker compose up` skips this)
- Run tests: `uv run pytest` (coverage: `uv run pytest --cov=src -q`)
- Lint/format: `uv run ruff format` then `uv run ruff check --fix`. The `scripts/dev_checks.sh` script applies formatting automatically, and VS Code formats on save via Ruff. See `scripts/format_all.sh` for a quick all-in-one solution.
- Type check: `uv run pyright`

### Local Docker Development

The `docker-compose.yaml` mounts source code into containers as read-only volumes (`./src:/app/src:ro`). This means:

- **Python changes**: Require `docker compose restart gateway` to pick up modifications
- **Static files (HTML/JS/CSS)**: Mounted live, but browsers cache aggressively. Use hard refresh (Cmd+Shift+R / Ctrl+Shift+R) to see changes
- **Config files**: Changes to `config/` files require container restart

Common commands:
- `docker compose restart gateway` - restart just the gateway after code changes
- `docker compose logs gateway --tail 50` - check recent gateway logs
- `docker compose ps` - verify container status

## Tooling

- Inspect the dev Postgres quickly with `uv run python scripts/query_debug_logs.py`. The helper loads `.env`, connects to `DATABASE_URL`, and prints recent conversation events and debug logs.

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

### Test Requirements

**IMPORTANT: Always write unit tests when adding or significantly modifying code.**

- New modules MUST have corresponding test files in `tests/unit_tests/` mirroring the source structure
- New functions/classes MUST have unit tests covering:
  - Happy path behavior
  - Edge cases and error conditions
  - Any format conversions or data transformations
- Refactored code MUST maintain or improve test coverage
- PRs without tests for new functionality will be considered incomplete

## Security & Configuration

- Keep lint, test, and type-check settings consolidated in `pyproject.toml`; avoid extra config files unless necessary.
- Copy `.env.example` to `.env`; never commit secrets.
- Key env vars: `DATABASE_URL`, `REDIS_URL`, `POLICY_CONFIG`, `PROXY_API_KEY`.
- Update `config/policy_config.yaml` rather than hardcoding.
- Validate setup with test requests to the gateway at `http://localhost:8000`.

## Policy Architecture

Policy instances are **singletons created once at startup** and shared across all concurrent requests. They must be stateless — no request-scoped data on the policy object.

- **Request-scoped state** belongs on `PolicyContext` (via `get_request_state()`) or on the IO object (`AnthropicPolicyIOProtocol`).
- **`PolicyContext`** is created per-request and flows through the entire request/response lifecycle. Use `get_request_state(owner, type, factory)` for typed per-policy state.
- **`AnthropicPolicyIOProtocol`** is the request-scoped I/O surface for Anthropic execution policies — it holds the mutable request, backend response, and streaming methods.
- **`freeze_configured_state()`** runs at policy load time and rejects mutable container attributes on the policy instance. Config-time collections should be immutable (tuple, frozenset).

## Design Methodology References

Frameworks used for requirements analysis, UI design, and scope simplification. Reference these when questioning scope or designing UIs.

**Musk's 5-Step Design Process:**
- [ModelThinkers summary](https://modelthinkers.com/mental-model/musks-5-step-design-process) — concise overview of all 5 steps
- [Everyday Astronaut / OODA Loop deep dive](https://oodaloop.com/analysis/ooda-original/the-everyday-astronaut-elon-musk-and-his-five-step-engineering-process/) — Musk explaining it in his own words
- Key rule: steps must happen IN ORDER. Most common error: optimizing something that shouldn't exist (Step 3 before Step 2).

**Nielsen's 10 Usability Heuristics:**
- [Official NN/g article](https://www.nngroup.com/articles/ten-usability-heuristics/) — the canonical source
- [Progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/) — the specific pattern we use for policy config

**UX Requirements & 5 Whys:**
- [5 Whys in UX Design (UX Planet)](https://uxplanet.org/the-5-whys-how-to-make-the-most-of-this-method-10c07885d9e) — practical guide
- [5 Whys in Design Thinking (Make Iterate)](https://makeiterate.com/the-5-whys-in-design-thinking-and-how-to-use-them/) — when and how to apply
- [Design Requirements (IxDF)](https://www.interaction-design.org/literature/topics/design-requirements) — requirements are problems, not solutions

**When to use which:**
- Musk's process: when simplifying an existing system or questioning scope
- Nielsen heuristics: when evaluating a UI design or identifying usability problems
- 5 Whys: when writing requirements — strip solutions, find root problems

## Policy Selection

- Policies are loaded from the YAML file pointed to by `POLICY_CONFIG` (default `config/policy_config.yaml`).
- Minimal YAML:

  ```yaml
  policy:
    class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
    config: {}
  ```
