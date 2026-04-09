# CHANGELOG

## Unreleased | TBA

## 3.0.0 | 2026-04-09

### Features

- **Remove OpenAI gateway and Codex support**: The proxy now exclusively supports the Anthropic `/v1/messages` endpoint. Removed `/v1/chat/completions`, LiteLLM request routing, and Codex CLI support. LiteLLM is retained only for policy-internal judge LLM calls. (#351)
- **Diff viewer auto-loads recent calls**: The `/diffs` page now automatically shows the recent calls list on load, instead of requiring a manual "Browse Recent" click.
- **Auto-release for luthien-proxy**: on merge to main, automatically compile changelog fragments, cut a versioned section in CHANGELOG.md, tag the release, and trigger GitHub Release + Docker image publishing. Starts at v3.0.0, auto-increments patch. Also fixes Docker images reporting `0.0.0+sha` instead of the actual version when built from a tag.
- **Chain-first policy config UX**: Overhaul policy configuration page with a unified chain-building experience. Click policies to preview details, press + to add to chain. Visible move/remove controls, blue/green color tinting for proposed/active chains, sticky Proposed and Active columns, proper Alpine.js config forms for chain items, and hidden internal policies by default. (#389)
- **CLI auto-versioning & publishing**: luthien-cli version is now derived from git tags (`cli-v*`) via hatch-vcs. Merging CLI changes to main auto-tags and publishes to PyPI. (#390)
- **`luthien policy` CLI command**: View, list, inspect, and switch gateway policies from the command line with interactive picker support. (#479)
- **Conversation viewer improvements**: Deduplicates cumulative API history, collapses preflight turns (quota probes, title generation), renders XML-tagged sections as collapsible blocks, and pairs tool calls with their results. Backend now extracts Anthropic-style tool_result content blocks with error state.
- **Credential management standardization**: Introduce `Credential` value object and `AuthProvider` config system for typed credential handling across gateway, policies, and judge calls. Add server credential store with optional encryption, admin API for credential CRUD, and `auth_provider` config field for judge policies.
- **`luthien hackathon` command**: One-command hackathon onboarding — forks/clones repo, installs deps, starts gateway from source, interactive policy picker, and prints comprehensive getting-started guide with cheatsheet, UI tour, key files, and project ideas.
  - New `HackathonOnboardingPolicy`: first-turn welcome with hackathon context
  - New `hackathon_policy_template.py`: SimplePolicy skeleton for participants to customize (#397)
- **Docker-free local mode**: `luthien onboard` now defaults to local mode — SQLite database, in-process event publisher, no Docker required. Use `--docker` for the previous PostgreSQL + Redis setup. (#370)
- **Onboarding policy**: New `OnboardingPolicy` appends a welcome message with config links to the first response in a conversation, then becomes inert on subsequent turns.
  - `luthien onboard` now uses the onboarding policy by default (no more policy selection step)
  - `luthien onboard` pre-seeds the onboarding prompt and opens the config page after gateway setup
- **`--proxy-ref` CLI option**: Run `onboard`, `up`, and `hackathon` against a specific branch, commit, or PR (`--proxy-ref '#123'`) instead of defaulting to main (#407)
- **Streaming protocol compliance validator**: Added a pipeline-level validator that checks Anthropic streaming event ordering after each stream completes. Logs warnings on violations (content blocks after message_delta, unclosed blocks, etc.) and records them as policy events and OTel span attributes. This is the architectural prevention for the class of streaming ordering bugs seen in PRs #134 and #356.
- **Synthesized policy configuration UI**: Three-column Available|Proposed|Active layout with PBC-aligned nav
  - Simple/Advanced policy grouping with inline In/Out examples
  - Pydantic/Alpine.js schema-driven config forms
  - Credential source dropdown and dual test panels
  - Single/Chain mode toggle, settings popover in nav
  - Redesigned landing page with progressive disclosure
  - PBC design tokens across all pages (Inter font, frosted glass nav)
  - Supersedes: #372, #376 (#379)
- **Global telemetry dashboard**: Added a Cloudflare Worker at `telemetry.luthien.cc` that relays anonymous usage metrics to Grafana Cloud for worldwide adoption tracking. Updated default telemetry endpoint from `telemetry.luthien.io` to `telemetry.luthien.cc`. (#460)
- **History page clarity**: The `/history` page is now titled "Sessions" with the subtitle "Each session is one conversation — click to view". Session cards show a `→` arrow that highlights green on hover, making clickability explicit. Sessions without a message preview are dimmed and italicised instead of showing "No preview available".
- **Version display**: Show proxy version (git commit hash) in CLI onboard output and as a shared footer on all web UI pages. Replaces hardcoded version strings with real build identifiers across all deployment modes. (#507)
- **Conversation viewer rewrite**: SSE-powered live updates (replacing polling), per-turn event timeline with raw JSON, and JSONL export
  - New `ConversationLinkPolicy` injects viewer URL into first response per session
  - JSONL export endpoint at `GET /api/history/sessions/{id}/export/jsonl` (#478)


- **In-process Redis replacement** — activity monitor, credential cache, and all Redis features work without Redis in local single-process mode via `EventPublisherProtocol` and `CredentialCacheProtocol` abstractions
- **Silence OTel errors** (silence-otel): Gracefully handle missing OTel/Tempo infrastructure
  - Default `OTEL_ENABLED` to `false` (opt-in instead of opt-out)
  - Silence gRPC and OTel exporter loggers that spam ERROR on connection failure
  - Docker Compose explicitly enables OTel when running the full stack
  - Log "OTel disabled" at DEBUG instead of INFO
- **CLI progress indicators** (cli-progress): Add spinners to long-running CLI operations so users know the tool isn't hung
  - `luthien onboard`: spinners during image pull, container stop/start, and health check
  - `luthien up` / `luthien down`: spinners during container start/stop and health check
  - `repo.py`: spinners during artifact download and update checks
- **Policy context injection** — injects a system message informing the LLM about active policies, preventing model confusion when policies modify output; configurable via `INJECT_POLICY_CONTEXT` env var (#355)
- **SQLite support** for Docker-free installs (#344)
- **`luthien onboard`** interactive setup command — prompts for policy description, generates keys, starts stack (#317)
- **Auto-fetch proxy artifacts on onboard** — `luthien onboard` downloads Docker artifacts from GitHub, no repo checkout needed (#345)
- **Mock e2e testing framework** — real HTTP requests against a fake Anthropic backend, no API calls or cost (#307)
- **Surface upstream billing mode** to prevent unexpected API charges (#311)

### Fixes

- **Batch env var validation**: Report all missing required environment variables at once instead of failing on the first one. (#416)
- **Auto-start gateway from `luthien claude`**: `luthien claude` now automatically starts the gateway if it isn't running, instead of letting Claude Code fail with ConnectionRefused. (#403)
- **Docker entrypoint SQLite fix**: Skip Postgres migrations when DATABASE_URL is a SQLite URL, and filter `sqlite_schema.sql` from the Postgres migration glob. (#415)
- **Docker local build fallback**: When `docker compose pull` fails (e.g. GHCR 403), onboarding now offers to clone the repo and build images locally instead of exiting. (#455)
- **Docker onboard error messaging**: Narrow GHCR auth-failure detection from bare "denied" to "access denied" to avoid false positives on Docker socket permission errors, and strengthen test coverage for `_download_files` error paths. (#454)
- **Fix hackathon command crash**: Remove `import yaml` re-introduced by the hackathon command after pyyaml was dropped as a dependency. (#402)
- **Sanitize client-facing error detail leakage**: Replace raw `str(e)` exception messages in HTTP responses with generic messages across pipeline, admin, and history routes. Internal details (Pydantic traces, DB errors, module paths) are now logged server-side with `repr(e)` only and never forwarded to clients. Set `VERBOSE_CLIENT_ERRORS=true` to restore verbose error details for local debugging. (#313)
- **Persist ADMIN_API_KEY to .env during local onboard**: `luthien onboard` now writes `ADMIN_API_KEY` to the gateway `.env` file, preventing auth failures after gateway restart caused by the gateway generating a new random key on each startup.
- **Onboarding QA fixes**: Multiple fixes from first-round QA testing
  - Install script now checks for working `git` before proceeding (macOS Xcode CLI tools)
  - Fixed wrong Claude Code package name in error message (`claude-cli` → `claude-code`)
  - Added transparent `/v1/*` API passthrough so Claude Code endpoints beyond `/v1/messages` don't 404
  - Fixed Sentry crash on invalid DSN by validating URL format before `sentry_sdk.init()`
  - Fixed gateway port instability by stopping old gateway before selecting a new port
  - Fixed Claude Code TUI freeze when launched via `curl | bash` by reopening stdin from the real pty device
  - Fixed 500 errors on non-streaming requests for Opus models by using streaming internally in `complete()` (#490)
- **Fix CLI install and CI publish pipeline**: CI workflows (`auto-tag-cli`, `release-cli`) were failing because `uv run pytest` didn't install dev extras. Install scripts now pull from GitHub source instead of stale PyPI package. (#404)
- **ConversationLinkPolicy**: Link now appears in the actual conversation response instead of being silently consumed by Claude Code's invisible preflight call
- **Policy diff viewer**: Fix "str object has no attribute isoformat" error when loading recent calls on SQLite
- **Fix broken e2e tests and add single-command test runner**: Replace fragile module-level monkey-patching with pytest fixture overrides for e2e test config (gateway_url, api_key, auth_headers). Fixes 40 sqlite_e2e tests broken since PR #410, plus 5 mock_e2e test failures from hardcoded ports, stale Docker fallbacks, and import-time settings caching. Adds `scripts/run_e2e.sh` to orchestrate all e2e tiers (sqlite, mock, real) with automatic setup/teardown — no Docker needed for sqlite or mock tiers. All 210 tests (42 sqlite + 168 mock) now pass from a single `./scripts/run_e2e.sh sqlite mock` invocation. (#494)
- **launch_claude_code.sh starts wrong service**: The script called `observability.sh up -d` instead of `start_gateway.sh` when the gateway health check failed, so the gateway never actually started. (#452)
- **OnboardingPolicy in MultiSerialPolicy chains**: Fixed two bugs preventing proper composition.
  - OnboardingPolicy hook methods silently failed because `context.request` is always `None` in the Anthropic path. Now stashes the request via `get_request_state()`.
  - `MultiSerialPolicy.on_anthropic_stream_complete` now chains each policy's emissions through remaining policies' `on_anthropic_stream_event`, so downstream transforms (e.g. AllCapsPolicy) apply to all content including welcome messages. (#409)
- **Sanitize Redis URL in logs**: Strip credentials from Redis connection URL before logging to prevent credential exposure. (#482)
- **Prevent Sentry initialization during test runs**: litellm's `load_dotenv()` was picking up `SENTRY_ENABLED=true` from the repo's `.env` before the test guard could run, causing test exceptions to be sent to production Sentry. Moved the guard to module-level in `tests/conftest.py` with force-set instead of `setdefault`. (#486)
- **Anthropic prompt cache tokens now forwarded**: `cache_creation_input_tokens` and `cache_read_input_tokens` are included in the response usage object when present, both for non-streaming and streaming responses. Previously these were silently dropped, preventing users from tracking prompt caching effectiveness.
- **Reduce gateway memory footprint to fit 1G Docker limit**: Skip duplicate raw-event buffering during streaming, make client cache size configurable via `ANTHROPIC_CLIENT_CACHE_SIZE`, and validate `LOG_LEVEL` at startup. (#453)
- **Fix local onboarding and Docker port conflicts**: Install `luthien-proxy` from GitHub instead of PyPI (not yet published). `luthien up` in Docker mode now auto-selects free ports for conflicting services and saves the resolved gateway URL to config so `luthien claude` routes correctly. (#385)
- **Onboarding discoverability improvements**: Add "next step" nudge in README after setup, detect Docker Compose v1 with a specific upgrade message in quick_start.sh (#456)
- **Fix onboarding crashes**: Bundle `sqlite_schema.sql` with the Python package so the gateway can create database tables in pip-installed environments. Remove `pyyaml` dependency from CLI by writing config YAML directly.
  - Gateway no longer crashes with "no such table: current_policy" on fresh `luthien onboard`
  - `_write_policy` no longer fails with `ModuleNotFoundError: No module named 'yaml'`
  - README now explains dashboard API key requirements and localhost auth bypass (#399)
- **Optional ADMIN_API_KEY**: Gateway no longer crashes on startup when `ADMIN_API_KEY` is unset — admin endpoints handle the missing key gracefully at request time instead. (#405)
- **PROXY_API_KEY no longer required**: The proxy no longer requires `PROXY_API_KEY` to be set — `AUTH_MODE=both` degrades gracefully to passthrough-only. Neither `luthien onboard` nor `luthien hackathon` generate a proxy key. `AUTH_MODE=proxy_key` without a key is now a hard startup error. (#476)
- **Reject missing max_tokens**: Requests without `max_tokens` now return 400 instead of silently defaulting to 4096. Removed hallucinated `max_output_tokens` alias. Matches real Anthropic API behavior.
- **Fix session ID not recorded in OAuth passthrough mode**: Fall back to `x-session-id` header when `metadata.user_id` is absent or doesn't match the API key session format. Conversation history is now recorded for OAuth users. (#386)
- **Inject error message when judge failure silently strips all content**: When `on_error: block` is configured and the safety judge fails (auth error, network, rate limit), all content blocks were previously dropped with no explanation — the gateway returned an empty response with `stop_reason: end_turn` and Claude Code showed "Cogitated for Xs" then nothing. The gateway now injects an error text block in both the non-streaming and streaming paths explaining that the response was blocked due to a judge failure. (#451)
- **Single-pass dev_checks.sh**: Removed the pre-clean-tree check and auto-stages formatting fixes instead of failing. No more two-pass commit dance.
  - Script paths now use `git rev-parse --show-toplevel` for worktree compatibility (#422)
- **Streaming pipeline leaked Python SDK synthetic events to wire-protocol clients**: The Anthropic Python SDK's high-level `MessageStream` injects synthetic helper events (`text`, `thinking`, `citation`, `signature`, `input_json`) that have no wire-protocol counterpart. These were forwarded to clients, breaking strict validators like `@ai-sdk/anthropic`. Fixed by switching from `messages.stream()` (high-level `MessageStream` with synthetic events) to `messages.create(stream=True)` (raw `AsyncStream[RawMessageStreamEvent]` yielding only wire-protocol events). This eliminates the problem structurally — no blocklist/allowlist maintenance required. (#499)
- **Fix streaming protocol violation in SimpleLLMPolicy**: When tool_use blocks are blocked by the judge, emit an explanatory text block (e.g., `[Tool call `Bash` was blocked by policy]`) instead of an orphaned `content_block_stop` or empty response. This fixes the Anthropic streaming protocol violation and allows Claude Code to continue the conversation after a tool call is blocked. (#443)


- Fix worktree dev instances sharing `COMPOSE_PROJECT_NAME` — auto-derive from directory name (#348)
- Make gateway API key optional for `luthien claude` — OAuth passthrough by default (#346)
- Fix onboard port conflicts and add API key warning (#341)
- Return Anthropic-format errors for `/v1/messages` HTTPExceptions (#315)
- Explicit backend timeout (`ANTHROPIC_BACKEND_TIMEOUT_SECONDS = 600`) and safe policy error handling (#310)
- Stop setting `ANTHROPIC_API_KEY` in launch scripts — let Claude Code use its own credentials (#318)
- Inject warning on SimpleLLMPolicy judge failure instead of silent pass-through (#329)
- Fix silent policy class errors in `_load_from_db()` — no longer silently downgrades to YAML config (#327)
- Narrow broad except in file-fallback-db policy init to `FileNotFoundError` (#328)
- Narrow bare except to `ValueError` in admin JSON parsing (#326)
- Narrow DB exception handling and add drop counters (#330)
- Add logging to 12 silent exception handlers across codebase (#338)
- Fix test-chat Docker selfcall, nullable param example, pair input UI (#306)
- Show Deactivate button instead of Reactivate for active policy on `/policy-config` (#333)
- Clear nav billing badge polling interval on Alpine destroy (#323)
- Fix mock e2e tests on Linux with correct Docker networking and auth (#331)
- Fix `quick_start.sh` health check reliability (#289)
- Add `--build` to `quick_start.sh` to prevent stale Docker images (#299)
- Sanitize sensitive headers in DebugLoggingPolicy (#301)
- Fix MultiParallelPolicy deepcopy crash and MultiSerialPolicy response ordering (#305)
- Fix overseer test harness usability improvements (#294)
- Fix hardcoded database name in migration 008 (#281, #282)
- Add dirty-tree warning to `dev_checks.sh` (#324)

### Refactors

- **Billing badge accessibility & polish**: Remove duplicate tab stop on badge, skip tooltip repositioning when hidden, replace Unicode escapes with literal characters, and add spatial-separation comment for the body-appended tooltip. (#477)
- **Consolidate conversation views**: Merged `/activity/monitor`, `/history/session/{id}`, and `/conversation/live/{id}` into a single live conversation viewer at `/conversation/live/{id}`
  - Session list at `/history` now links directly to the live view
  - Live view renders turns incrementally (new turns slide in without re-rendering existing ones)
  - Raw event stream viewer preserved at `/debug/activity` for low-level debugging
  - Old URLs (`/activity/monitor`, `/history/session/{id}`) 301-redirect to their replacements (#501)
- **Unify policy interface to hooks-only**: Replace dual `run_anthropic`/hooks execution model with hooks as the sole interface. Eliminates ~660 lines of duplicate logic and the bug class from PR #409.
  - Remove unused `MultiParallelPolicy`
  - `AnthropicExecutionInterface` protocol now defines 4 hook methods instead of `run_anthropic`
  - Executor owns backend I/O; policies only implement hooks (#421)
- **Dual SQLite/Postgres migration sync**: Replace the hand-maintained SQLite schema snapshot with incremental per-dialect migration files. SQLite migrations now run incrementally (matching Postgres behavior), with automatic bootstrap for existing databases. A CI schema comparison test catches drift between the two dialects. (#442)
- **Remove dead match entry in parse_judge_response**: Remove unreachable `"```json"` from the prefix match set — `lstrip("`")` already strips all backticks before the check. (#483)
- **Switch pytest to --import-mode=importlib**: Eliminate test filename collision workarounds and sys.path hacks by using fully-qualified module paths for test imports. Shared constants moved to `tests/constants.py`.
- **Remove prefix-based credential type heuristic**: The gateway now relies solely on the transport header (`Authorization: Bearer` vs `x-api-key`) to determine credential type. Removed `is_anthropic_api_key()` and the `oauth_via_api_key` credential type that second-guessed the transport header using token prefix inspection.
- **Remove dead code from ToolCallJudgePolicy**: Delete unused `_call_judge_with_failsafe()` and `_create_judge_failure_message()` methods left over from a previous refactor. (#445)
- **Reorganize tests by package**: Move tests into `tests/luthien_proxy/` and `tests/luthien_cli/` subdirectories. Add `luthien-cli` as an editable dev dependency so CLI tests run from the root venv. (#410)
- **Type strictness pass**: Replace `Any` with concrete types (`AnthropicContentBlock`, `ToolCallDict`, `JSONObject`, `AnthropicRequest`) across 12 files; remove 208 lines of dead code (`transaction_recorder.py`). Also fixes a latent `AttributeError` in `PolicyContext.__deepcopy__` where `.model_copy()` was called on a non-Pydantic field. (#461)


- Move luthien-cli into `src/` for consistent project layout (#321)
- Encapsulate credential type tracking in CredentialManager (#325)

### Chores & Docs

- **CLI install: pipx → uv tool**: All references to `pipx` replaced with `uv tool install`. Install scripts now auto-detect and migrate existing pipx installations.
- **COE follow-up for PR #356**: Added streaming event ordering invariant to `dev/context/gotchas.md` — all content blocks must precede `message_delta` in Anthropic streaming protocol
- **Dockerless default**: Documentation and `.env.example` now default to dockerless mode (SQLite, no Postgres/Redis) for development and single-user local use. Docker Compose with Postgres+Redis is positioned for multi-user production deployments. `quick_start.sh` now validates that DATABASE_URL isn't SQLite before starting Docker services. (#391)
- **OWASP threat scenario e2e tests**: 48 new mock_e2e tests covering LLM01 (Prompt Injection), LLM06 (Sensitive Disclosure), LLM08 (Excessive Agency), gateway robustness, and audit trail (8+8+16+11+5 across 5 files).
  - OWASP LLM markers (llm01/02/04/06/07/08) added to pytest config for selective test runs
  - Fixed 4 pre-existing test failures: passthrough auth tests now use `MOCK_ANTHROPIC_HOST` env var (defaults to `host.docker.internal`, overridable to `localhost` for dockerless/CI runs); `_enable_request_logging` fixture is a no-op when `ENABLE_REQUEST_LOGGING` is already set in the environment (#458)
- **Move dev tools to dev dependencies**: Moved `pyright`, `vulture`, and `pytest-timeout` from production `[project.dependencies]` to the `[dependency-groups] dev` group so they aren't pulled in by `pip install luthien-proxy`. (#444)
- **OAuth passthrough docs**: Restructured README Configuration section to lead with OAuth passthrough as the default auth mode. Added prominent billing warning for API key mode. (#468)
- **Policy reference guide**: Add comprehensive `docs/policies.md` with full examples for all policies, presets, composition patterns, admin API usage, and custom policy authoring guide. Update README available policies section.
- **Docs & UI polish**: Switched judge model examples from gpt-4o-mini to Haiku, clarified probability_threshold comment, explained YAML `class:` field, renamed "Dogfood mode" to "Safety mode" on policy-config page (#491)
- **README preset policies**: Added all 7 built-in preset policies to the README's available policies section, organized into "Built-in Presets" and "Core Policies" subsections
- **Remove OpenAI/GPT references**: Cleaned up config files, deploy docs, startup scripts, and .env.example to remove OPENAI_API_KEY references and replace GPT model examples with Claude/Anthropic equivalents. Functional code (OpenAI format pipeline, GPT model detection in judge utils) left intact. (#503)
- **Synchronize local and CI dev checks**: Pin pyright version, enforce locked dependency sync, require shellcheck, and fail on uncommitted formatting changes — eliminating common sources of local/CI divergence.
- **ToolCallJudgePolicy unit tests**: Add comprehensive unit tests for ToolCallJudgePolicy covering streaming tool pass/block, non-streaming responses, state cleanup, and blocked message formatting. Extract shared Anthropic streaming event builders into reusable test helper module. (#448)
- **Trivial local startup from source**: `scripts/start_gateway.sh` now auto-creates `.env` from `.env.local.example` when no `.env` exists, eliminating the manual copy step for first-time dev setup. README `## Development` section now includes a "Quick Start (from source, no Docker)" subsection with the full clone-to-running sequence. (#471)
- **Add uninstall instructions to README**: New `## Uninstall` section covers stopping the gateway, removing the CLI (`uv tool uninstall luthien-cli`), and cleaning up the data directory (`~/.luthien`), for both local and Docker modes. (#473)

- Add shellcheck to `dev_checks.sh` and fix all 22 warnings across 9 scripts (#332)
- Update README to match landing page v10.7 (#319)
- Add ARCHITECTURE.md codebase map (#293)
- Document bash 3.2+ requirement on all shell scripts (#320)
- Bump luthien-cli to 0.1.7 (#342)
- Update claude-code-action to v1 (#283)
- Remove `dev/TODO.md`, track TODOs on Trello (#300)
- Remove dead persistence pipeline (`storage/persistence.py`) (#285)
- Define `DEFAULT_TEST_MODEL` constant (#288)
- Add `.claude/worktrees/` to `.gitignore` (#279)
- Update Railway deployment config (#334)

### Previously logged (pre-#306)

- Remove dead Anthropic compatibility handlers (`_handle_streaming`, `_handle_non_streaming`) from `anthropic_processor.py`
  - Update unit/regression tests to exercise `process_anthropic_request()` instead of deleted internal wrappers
  - Refresh docs/examples to use current policy classes and Anthropic execution runtime terminology
- Remove dead Anthropic streaming executor path
  - Delete unused `src/luthien_proxy/streaming/anthropic_executor.py`
  - Delete executor-only unit tests and migrate requirement regressions to assert streaming behavior through `process_anthropic_request()`
  - Tighten Anthropic execution-runtime type guard and add tests for invalid streaming emissions and upstream `io.complete()` failures

- Refactor: extract `restore_context()` context manager for OpenTelemetry span context management
  - Replaces manual `attach`/`detach` pattern in both `anthropic_processor.py` and `processor.py`
  - Guarantees cleanup even on exception, reduces nesting depth, improves readability

- Fix Anthropic observability pipeline: events not written to DB, generic error types, empty conversation history (#249)

- Fix default auth_mode from `proxy_key` to `both` so Claude Code OAuth works on fresh setups (#222)
  - DB migration: `008_default_auth_mode_both.sql`
  - Also updates existing `proxy_key` rows to `both`

- Add general-purpose policy composition API (policy-composition)
  - `compose_policy()` function for inserting policies into chains at runtime
  - `MultiSerialPolicy.from_instances()` for building chains from pre-instantiated policies
  - `DogfoodSafetyPolicy` — regex-based safety policy that blocks dangerous commands
    (docker down, pkill, rm .env, DROP TABLE) when proxying through the gateway
  - `DOGFOOD_MODE` env var to auto-inject DogfoodSafetyPolicy into any policy chain
  - Replaces hacky approach from #243 with clean, reusable composition mechanism

- Fix SamplePydanticPolicy crash on activation (#250)
- Add MultiSerialPolicy and MultiParallelPolicy for composing control policies (#184)
  - MultiSerialPolicy: sequential pipeline where each policy's output feeds the next
  - MultiParallelPolicy: parallel execution with configurable consolidation strategies
    (first_block, most_restrictive, unanimous_pass, majority_pass, designated)
  - Both support OpenAI and Anthropic interfaces with interface compatibility validation
  - Shared `load_sub_policy` utility for recursive policy loading from YAML config
- Add configurable passthrough authentication (passthrough-auth)
  - Three auth modes: `proxy_key`, `passthrough`, `both` (default) - configurable at runtime via admin API
  - Credential validation via Anthropic's free `count_tokens` endpoint with Redis caching
  - Configurable TTLs for valid (1hr default) and invalid (5min default) credential cache
  - Admin API: `GET/POST /admin/auth/config`, `GET/DELETE /admin/auth/credentials`
  - Admin UI: `/credentials` page for managing auth modes and viewing cached credentials
  - Supports OAuth token passthrough for Claude Code
  - `x-anthropic-api-key` header still supported for explicit client key override
  - DB migration: `007_add_auth_config_table.sql`

- Add `/client-setup` endpoint with setup guide for connecting Claude Code to the proxy (deploy-instructions)

- Add conversation live view with diff display (#186)
  - New `/conversation/live/{id}` endpoint for real-time conversation monitoring with diff visualization
  - "Live View" link from history detail page
  - E2E tests for conversation live view

- Fix login redirect to send user back to original page after auth (#195)
  - Hidden form field was named `next` but POST handler expected `next_url`
  - Integration tests for redirect behavior

- Add multi-turn e2e test with /compact for Claude Code sessions (#182)
  - `test_claude_code_multiturn_with_compact` exercises full multi-turn session lifecycle through the proxy
  - `run_claude_code()` now supports `resume_session_id` parameter for `--resume`

- Support multiple dev docker deployments on the same machine (#183)
  - Parameterize all hardcoded ports in `docker-compose.yaml` via env vars with sensible defaults
  - `COMPOSE_PROJECT_NAME` in `.env.example` isolates networks, volumes, and container names per deployment

- Add Apache 2.0 LICENSE file (#181)

- Move internal planning docs to luthien-org (#173)
  - Remove 38 historical planning files from `dev/archive/` and 4 outdated v1 docs from `docs/archive/`
  - Reduces noise for contributors in the public repo

- Document web UI consolidation strategy with endpoint inventory (#189)

- Fix E2E test failures: docker env override and metadata validation (#172)
  - Fix shell env vars overriding `.env` file API keys
  - Update tests for Anthropic API metadata validation changes

- Fix SimplePolicy non-streaming support (#147, #168)
  - SimplePolicy-based policies previously only worked for streaming responses
  - Add `on_response()` hook so policies work when `stream: false`

- Forward backend API errors to clients with proper format (#146)
  - Backend LLM errors (auth failure, rate limit, invalid request) now return properly formatted responses matching the client's API format
  - Previously caused generic 500 errors that made clients like Claude Code hang

- Fix compatibility issues caused by litellm update (#143)

- Refactor: stricter typing in history service (#139)
  - Add `event_types.py` with TypedDicts for structured event data
  - Discriminated unions for content blocks (text, tool_use, tool_result, image)
  - Replace `dict[str, Any]` with proper typed dicts

- Refactor: use dedicated `thinking_blocks` field instead of overloading content (#138)
  - Add `ThinkingBlock` and `RedactedThinkingBlock` TypedDict types
  - Revert `content` back to `str | None` with separate `thinking_blocks` field

- Improve gateway homepage (#132)
  - Add missing UI links (`/policy-config`, `/history`)
  - Add "Auth Required" badges to protected endpoints
  - Add Quick Start shortcuts for common tasks

- Fix docker-compose project name collision across worktrees (fix/docker-project-names)
  - Derive `COMPOSE_PROJECT_NAME` from worktree directory name (e.g. `luthien-main`, `luthien-deploy-instructions`)
  - Add `name:` field to `docker-compose.yaml` with `luthien` default for raw `docker compose up`
  - Comment out `COMPOSE_PROJECT_NAME` in `.env.example` so new setups get auto-derivation

- Remove Grafana, Loki, and Promtail from observability stack (remove-loki-grafana)
  - Keep Tempo for distributed tracing and OpenTelemetry instrumentation
  - Remove `observability/grafana/`, `observability/grafana-dashboards/`, `observability/loki/`, `observability/promtail/` directories
  - Remove Grafana/Loki/Promtail services from docker-compose.yaml
  - Remove `GRAFANA_URL` setting from `.env.example` and `Settings` class
  - Update `build_tempo_url()` to generate direct Tempo API URLs instead of Grafana Explore URLs
  - Update `scripts/observability.sh` for Tempo-only stack
  - Remove `scripts/test_observability.sh` (was Loki-dependent)
  - Update all documentation references

- Add SaaS infrastructure provisioning CLI for Railway (saas-infra)
  - New `saas_infra/` package with CLI for managing multi-tenant proxy instances
  - Commands: create, list, status, delete, redeploy, cancel-delete, whoami
  - Each instance gets isolated Railway project with Postgres + Redis + gateway
  - Soft delete with 7-day grace period before permanent deletion
  - Railway GraphQL API integration via httpx
  - JSON output mode for scripting (`--json` flag)
  - See `saas_infra/README.md` for usage documentation

- Fix E2E test failures and multi-event streaming support (#174)
  - `on_anthropic_stream_event` returns `list[AnthropicStreamEvent]` instead of single event
  - Policies can now emit multiple events per input (e.g. `[delta, stop]`)
  - SimplePolicy returns both events directly, removing `get_pending_stop_event` hack
  - ToolCallJudgePolicy streaming now works: blocked calls emit replacement text, allowed calls re-emit buffered events
  - Fix Claude Code E2E auth (`ANTHROPIC_AUTH_TOKEN` → `ANTHROPIC_API_KEY`)
  - Remove unsupported cross-format routing tests (Phase 2)
  - All 9 previously-failing E2E tests resolved

- Remove local Ollama container and all related configuration
  - Deleted docker/Dockerfile.local-llm, docker/local-llm-entrypoint.sh
  - Deleted config/local_llm_config.yaml, config/archive/demo_judge.yaml
  - Removed local-llm service and local_llm_models volume from docker-compose.yaml
  - Updated documentation to remove Ollama references

- Refactor policies to use platform-specific interfaces (split-apis)
  - Add `BasePolicy`, `OpenAIPolicyInterface`, `AnthropicPolicyInterface` ABCs
  - Unified policies implement both OpenAI and Anthropic interfaces
  - Rename hooks to `on_openai_*` and `on_anthropic_*` for clarity
  - Processors use `isinstance` checks for interface dispatch
  - Delete `policies/anthropic/` directory - all policies now in main `policies/`
  - Delete deprecated `AnthropicPolicyProtocol`

- Fix StringReplacementPolicy dropping finish_reason causing blank responses in Claude Code
  - Content and finish_reason must be emitted as separate chunks
  - SSE assembler's `convert_chunk_to_event()` returns early on content, ignoring finish_reason
  - Added e2e test to verify complete SSE event structure (message_delta, content_block_stop)

- Reorganize LLM types into separate OpenAI and Anthropic modules (#117)
- Fix thinking blocks stripped from non-streaming responses (#128)

- Pass through extra model parameters like `thinking`, `metadata`, `stop_sequences` (thinking-flags)
  - Anthropic requests now preserve all extra parameters during format conversion
  - Map `stop_sequences` (Anthropic) → `stop` (OpenAI)
  - Convert `tool_choice` format between Anthropic and OpenAI APIs
  - OpenAI requests already preserved extra params via Pydantic `extra="allow"`
  - Enables extended thinking, reasoning effort, and other provider-specific features
  - 14 new e2e tests validate parameter pass-through for both client types

- Auto-discovering policy configuration UI (policy-config-ui)
  - `/admin/policy/list` now auto-discovers all policies from `luthien_proxy.policies`
  - Config schemas extracted from constructor signatures using type hints
  - Policy config UI (`/policy-config`) generates form fields based on schema
  - Simple types get appropriate inputs (text, number, checkbox)
  - Complex nested types (dict, list) get JSON textarea
  - Fixes broken create/activate endpoints that didn't exist

- Add Railway demo deployment configuration (`railway.toml`, `deploy/README.md`)

- Add conversation history viewer with styled message types and markdown export (conversation-history-viewer)
  - Browse recent sessions at `/history` with turn counts, policy interventions, and model usage
  - View full conversation detail at `/history/session/{id}` with message type styling (system/user/assistant/tool call/tool result)
  - Policy annotations shown inline on turns that had interventions
  - Export any session to markdown via `/history/api/sessions/{id}/export`

- Improve conversation history list UI (#133)
  - Add first user message preview for at-a-glance session recognition
  - Add quick filters: Today, This week, Last week, Last 30 days, Claude Code, Codex
  - Add "More filters" dropdown with sort options (newest, oldest, longest, shortest) and policy activity filters
  - Sticky search/filter bar with magnifying glass icon
  - Date grouping (Today, Yesterday, day names, full dates)
  - Consistent green (#4ade80) color scheme matching other Luthien pages

- Increase unit test coverage from 84% to 90% (#115)
- Fix validation error when images in Anthropic requests (#103, #104)
- Migration validation and fail-fast checks (#110)
  - `run-migrations.sh` validates DB state against local files before applying
  - Gateway startup check ensures all migrations are applied
  - Fails fast with clear errors if: migrations missing locally, unapplied migrations, or hash mismatch
  - Records content_hash for each migration to detect modifications

- Improve login page UX (dogfooding-login-ui-quick-fixes)
  - Add show/hide password toggle below input field (avoids conflict with password managers)
  - Add clickable dev key hint for development environments
  - Add guidance for production users to check .env or contact admin
- Structured span hierarchy for request processing (luthien-proxy-a0r)
  - All pipeline phases (process_request, policy_on_request, send_upstream, process_response) are now visible as siblings in Grafana/Tempo
  - Add `luthien.policy.name` attribute to root span for easy policy identification
  - Add `request_summary` and `response_summary` fields to PolicyContext for policy-defined observability

- Dependency injection for `create_app()` (#105)

- Session ID tracking for conversation context (#102)
  - Extract session ID from Anthropic `metadata.user_id` (Claude Code format: `user_<hash>_account__session_<uuid>`)
  - Extract session ID from `x-session-id` header (OpenAI format)
  - Persist session ID to database for querying conversations by session
  - Add `RawHttpRequest` dataclass to capture original HTTP request data
  - Add OpenTelemetry span attributes for session tracking (`luthien.session_id`)
  - Debug API now returns session_id in call listings and event responses

- Unify OpenAI and Anthropic endpoint processing (#92)
- Fix broken migration script that prevented migrations from running (#fix-migration-script)
- Replace magic numbers with named constants [constants.py](src/luthien_proxy/utils/constants.py)

- Session-based login for browser access to admin/debug UIs (#88)
  - Add `/login` page with session cookie authentication
  - Protected UI pages (`/activity/monitor`, `/diffs`, `/policy-config`) redirect to login when unauthenticated
  - Sign out links on all protected pages
  - Backwards compatible: API endpoints still accept Bearer token and x-api-key

- Confirmed policy config UI backend integration already complete via PR #66 (feature/policy-ui-backend)

- Centralize environment configuration with pydantic-settings (#refactor/env-config-centralize)
  - Add `Settings` class in `src/luthien_proxy/settings.py` for typed configuration
  - Replace scattered `os.getenv()` calls throughout codebase with centralized settings access
  - Support `.env` file loading via pydantic-settings
  - Add `clear_settings_cache()` for test isolation

- Remove unused prisma dependency (#84)
- Added auth to debug endpoints (#86)
- Inject EventEmitter via DI instead of global state (#dependency_injection)
- Added e2e tests that actually invoke claude code running through the proxy

- Codebase cleanup (#81)
  - Remove dead code: `control_plane/` (stale pycache), `streaming_aggregation.py`
  - Standardize on Python module docstrings (removed ABOUTME convention)
  - Organize and deduplicate TODO.md
  - Update CLAUDE.md and codebase_learnings.md to reflect actual module structure

- Implement trace (tempo) + log (loki) observability

- Add `on_streaming_policy_complete()` lifecycle hook for cleanup (#76)
  - New policy hook called in finally block after all streaming policy processing completes
  - Guarantees cleanup runs even if errors occurred during policy processing
  - Implement buffer cleanup in ToolCallJudgePolicy using new hook
  - Simplify `_validate_tool_call_for_judging()` to return just the tool_call dict

- Streaming and Anthropic client fixes (#75)
  - Fix streaming tool calls missing `message_delta` for Anthropic clients
  - Refactor `AnthropicSSEAssembler` to `streaming/client_formatter`
  - Explicitly implement `ClientFormatter` protocol
  - Fix `ChatCompletionMessageToolCall` typing
  - Remove model registration logic

- Fix ToolCallJudgePolicy inheritance to use BasePolicy instead of PolicyProtocol (#62)
  - Resolves gateway startup failure when ToolCallJudgePolicy is configured
  - Override `on_chunk_received()` to prevent duplicate token streaming bug
  - Fix test mock signature to match `call_judge()` parameters
- Dependency injection improvements (#dependency-injection)
  - Add `Dependencies` container class for centralized service management
  - Create FastAPI `Depends()` functions for type-safe dependency access
  - Derive `event_publisher` lazily from `redis_client` (no duplicate storage)
  - Create `LLMClient` once at startup instead of per-request instantiation
  - Replace `getattr(app.state, ...)` pattern with proper DI

- Observability improvements (#observability-refactor)
  - Refactored `LuthienPayloadRecord` → `PipelineRecord` with simplified all-primitive interface
  - Renamed `payload_type` → `pipeline_stage` for better semantics
  - Optimized label structure for efficient querying (only low-cardinality fields as labels, high-cardinality fields are structured metadata)
  - clarified observability functions; simplified implementations
  - Added utility scripts for Loki validation ([query_loki_fields.py](scripts/query_loki_fields.py), [test_line_format.py](scripts/test_line_format.py))

- Policy authoring improvements (#57)
  - Add `BasePolicy` class with default implementations and convenience methods
  - Add convenience properties to `StreamingPolicyContext` (`last_chunk_received`, `push_chunk()`, `transaction_id`, `request`, `scratchpad`)
  - Comprehensive test coverage for policy callbacks and streaming behavior (1100+ new test lines)

- Remove "v2" concept and consolidate architecture (#55)
  - Moved all code from `src/luthien_proxy/v2/*` to `src/luthien_proxy/*`
  - Updated all imports from `luthien_proxy.v2.*` to `luthien_proxy.*`
  - Renamed `V2_POLICY_CONFIG` env var to `POLICY_CONFIG`
  - Renamed `config/v2_config.yaml` to `config/policy_config.yaml`
  - Updated route prefixes: `/v2/debug` → `/debug`, `/v2/activity` → `/activity`
  - Renamed docker service from `v2-gateway` to `gateway`
  - Moved test directories from `tests/**/v2/` to `tests/**/`

- Cleanup and refactoring (#50)
  - introduced `policy_core` for common streaming/policy utilities
    - moved core abstractions (`PolicyProtocol`, `PolicyContext`, `StreamingPolicyContext` to `policy_core`)
  - split `policies/utils.py` into focused modules `chunk_builders.py`, `response_utils.py`, `tool_call_judge_utils.py`
  - dependency analysis script

## 0.0.2 | 2025-11-07

- **Anthropic streaming fixes** (post-#49):
  - Add `AnthropicSSEAssembler` for stateful SSE event generation with proper block indices
  - Fix `ToolCallJudgePolicy` streaming: add `on_content_delta()`, fix chunk creation with proper `Delta` and `StreamingChoices` types
  - Add `DebugLoggingPolicy` for inspecting streaming chunks
  - 8 regression tests to prevent streaming bugs

- Refactor streaming pipeline to explicit queue-based architecture (#49)
  - Simplified `PolicyOrchestrator.process_streaming_response` to clear 2-stage pipeline
  - PolicyExecutor: Block assembly + policy hooks with background timeout enforcement
  - **TimeoutMonitor**: Dedicated class for keepalive-based timeout tracking (100ms check interval)
    - Detects stalled streams when no chunks arrive within configured threshold
    - Raises `PolicyTimeoutError` with timing details for debugging
    - Automatic keepalive reset on each chunk processed
  - ClientFormatter: Model responses to client-specific SSE format (OpenAI/Anthropic)
  - Explicit typed queues (`Queue[ModelResponse]`, `Queue[str]`) define data contracts
  - Dependency injection pattern for policy execution and client formatting
  - Comprehensive unit tests (32 policy executor tests including 8 timeout enforcement tests, 12 formatter tests)
  - Transaction recording infrastructure at pipeline boundaries

- Add `SimpleEventBasedPolicy` for beginner-friendly policy authoring (buffers streaming into complete blocks)
  - Example policies: `SimpleUppercasePolicy`, `SimpleToolFilterPolicy`, `SimpleStringReplacementPolicy`
  - Comprehensive unit and e2e test coverage

### V2 Architecture Migration ([#46](https://github.com/LuthienResearch/luthien-proxy/pull/46))

**Massive cleanup**: Deleted ~9,735 lines of V1 code, tests, and documentation (48% reduction) while building out V2 architecture.

**Major architectural redesign** from separate LiteLLM proxy + control plane to integrated FastAPI + LiteLLM architecture with event-driven policies and comprehensive observability.

#### Core Architecture ([b04d6cd](../../commit/b04d6cd))

- Integrated V2 gateway combining API gateway, control logic, and LLM integration in single process
- `ControlPlaneService` protocol supporting both local and future networked implementations
- `PolicyHandler` abstraction with event-driven interface for user policies
- Bidirectional streaming with policy control over request/response transformation
- Format converters for OpenAI ↔ Anthropic API compatibility
- Support for both streaming and non-streaming responses

#### Event-Driven Policy System

- New `EventDrivenPolicy` DSL with lifecycle hooks:
  - `on_chunk_started`, `on_content_chunk`, `on_tool_call_chunk`, `on_chunk_completed`
  - `on_request_started`, `on_request_completed`
  - `on_response_started`, `on_response_completed`
- `PolicyContext` for per-request state management and event emission
- `StreamingOrchestrator` for managing streaming response pipelines with timeout handling
- Reference implementations:
  - `NoOpPolicy` / `EventBasedNoOpPolicy` - Pass-through for testing
  - `UppercaseNthWordPolicy` - Text transformation demo
  - `ToolCallJudgeV3Policy` - LLM-based tool call security analysis

#### Observability Infrastructure ([8480e06](../../commit/8480e06), [5882493](../../commit/5882493))

- **OpenTelemetry Integration**:
  - Distributed tracing with Grafana Tempo
  - Automatic span creation for all gateway, control plane, and streaming operations
  - Custom `luthien.*` span attributes (call_id, model, stream status, chunk counts, policy decisions)
  - Trace context propagation through entire request pipeline
  - Log correlation via trace_id/span_id injection
  - OTLP gRPC exporter to Tempo

- **Real-Time Monitoring**:
  - Activity stream via Server-Sent Events (SSE) at `/activity/stream`
  - Live activity monitor web UI at `/activity/monitor` with filtering by call_id/model/event_type
  - Redis pub/sub for real-time event distribution
  - Automatic event publishing for gateway, streaming, and policy lifecycle

- **Debug & Analysis Tools**:
  - Debug API at `/debug/`:
    - `/calls` - List recent calls
    - `/calls/{call_id}` - Get call details
    - `/calls/{call_id}/diff` - Compare original vs transformed content
  - Diff viewer UI at `/diffs` with side-by-side JSON comparison
  - Links to Grafana Tempo traces from all UIs

- **Grafana Dashboards**:
  - Live activity dashboard with auto-refresh (control plane logs, V2 API requests, policy activity, errors)
  - Metrics dashboard (request rate by model, p95 latency, latency breakdown, recent traces)
  - Pre-provisioned dashboards auto-loaded on Grafana startup

- **Log Collection**:
  - Grafana Loki for centralized logging
  - Promtail for Docker container log collection
  - 24-hour retention with aggressive compaction
  - Automatic trace ↔ log correlation

#### V1 Cleanup ([slash-and-burn](../../tree/slash-and-burn))

- **Deleted ~18,000 lines of V1 code**:
  - V1 control plane implementation (separate FastAPI service)
  - V1 proxy integration (separate LiteLLM process)
  - Old callback-based streaming system
  - Legacy policy interfaces and event models

- **Removed Docker services**:
  - `litellm-proxy` (port 4000) - replaced by integrated V2 gateway
  - `control-plane` (port 8081) - merged into V2 gateway
  - `dummy-provider` (port 4015) - test fixture no longer needed

- **Archived documentation** (15 files):
  - `dev/archive/`: 7 completed planning documents
  - `docs/archive/`: 4 V1 architecture guides (v1-reading-guide, v1-developer-onboarding, v1-diagrams, v1-ARCHITECTURE)
  - `config/archive/`: 5 V1 config files + policies directory

- **Deleted 16 obsolete scripts**:
  - V1-specific: `build_replay_examples.py`, `dummy_control_plane.py`, `export_replay_logs.sh`
  - Demo artifacts: `demo_*.py`, `run_demo*.sh`
  - One-off spikes: `test_anthropic_streaming.py`, `test_judge_streaming.py`, etc.

- **Removed infrastructure**:
  - `docker/Dockerfile.litellm` - V1 LiteLLM proxy image
  - 8 environment variables (LITELLM_MASTER_KEY, CONTROL_PLANE_URL, LUTHIEN_POLICY_CONFIG, etc.)
  - Replaced `LUTHIEN_POLICY_CONFIG` → `POLICY_CONFIG`

- **Updated documentation**:
  - Migrated policy configuration examples to EventDrivenPolicy DSL
  - Updated port references (8081 → 8000, removed 4000)
  - Fixed service name references (control-plane → gateway)
  - Created `dev/ARCHITECTURE.md` with V2 core principles

#### Testing & Quality

- Comprehensive unit test coverage for policies, control plane, streaming orchestration
- Integration tests for V2 gateway endpoints
- End-to-end tests with real LLM providers (OpenAI, Anthropic, local Ollama)
- Docker-based testing with `./scripts/test_gateway.sh`
- Type safety with Pyright across all V2 modules

#### Developer Experience

- Single-command setup: `./scripts/quick_start.sh`
- Simplified service architecture: gateway, local-llm, db, redis
- Observability stack: `./scripts/observability.sh up -d`
- Live development with hot reload
- Launch scripts for Claude Code and Codex routing through gateway
- Comprehensive documentation:
  - `dev/event_driven_policy_guide.md` - Policy development guide
  - `dev/observability.md` - Observability features
  - `dev/VIEWING_TRACES_GUIDE.md` - Trace analysis walkthrough
  - `dev/OBSERVABILITY_DEMO.md` - Step-by-step demonstration

#### Configuration

- Single config file: `config/policy_config.yaml`
- Policy selection via class path + config dict
- Environment variables consolidated in `.env.example`
- Docker Compose profiles for optional services (observability)

#### Performance & Reliability

- Streaming pipeline with configurable timeouts
- Redis for ephemeral state and pub/sub
- PostgreSQL with Prisma for persistent state
- Graceful error handling with span error recording
- Health checks for all services
- Connection pooling and async I/O throughout

---

## 0.0.1 | 2025-10-10

**Initial V1 implementation** (archived)

- Basic LiteLLM proxy integration with separate control plane
- Callback-based streaming system
- Initial policy engine with tool call judging
- Database persistence with debug logs
- Redis for caching and ephemeral state
- Demo UI for trace visualization
- Hook-based extensibility system
