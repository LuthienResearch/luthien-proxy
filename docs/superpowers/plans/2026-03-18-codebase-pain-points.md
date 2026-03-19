# Codebase Pain Points Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce recurring confusion and time waste for agents/humans working in the codebase, based on analysis of 7 recent Claude Code sessions.

**Architecture:** Documentation improvements, test infrastructure fixes, small code cleanups. No large refactors — this is about reducing friction, not restructuring.

**Tech Stack:** Python, pytest, Markdown

---

## Summary of Findings

Analysis of 7 substantial Claude Code sessions (totaling ~15 hours of agent time) revealed these recurring pain categories:

1. **Docker doesn't work from worktrees** — agents waste 20-30 min per session discovering this
2. **ARCHITECTURE.md missing key info** — Redis roles, observability pipeline, external dependencies, worktree dev pattern
3. **Agent-written context docs propagate misinformation** — `dev/context/` files treated as ground truth when they contain agent inferences
4. **Test infrastructure friction** — coverage output drowns e2e results, orphaned test suites, duplicate test directories
5. **Naming confusion** — `REDIS_KEY_PREFIX` in backend-agnostic code, `config.py` vs `settings.py`
6. **`dev/context/authentication.md` contains incorrect OAuth info** — prefix-based detection documented as intentional when it's tech debt

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Modify | `ARCHITECTURE.md` | Add external deps section, observability pipeline, worktree dev guide |
| Modify | `CLAUDE.md` | Add worktree development pattern, warn about context doc reliability |
| Modify | `dev/context/README.md` | Add reliability warning about agent-written docs |
| Modify | `dev/context/authentication.md` | Fix incorrect OAuth detection docs |
| Modify | `dev/context/gotchas.md` | Add worktree/Docker gotcha, e2e coverage gotcha |
| Modify | `pyproject.toml` | Remove orphaned `saas_infra` from pyright include |
| Delete | `tests/unit_tests/test_overseer_*.py` | Orphaned tests (no corresponding source) |
| Delete | `tests/unit_tests/test_saas_infra/` | Orphaned tests (no corresponding source) |
| Modify | `tests/unit_tests/test_policy_core/` | Merge into `tests/unit_tests/policy_core/` |
| Modify | `src/luthien_proxy/credential_manager.py` | Rename `REDIS_KEY_PREFIX` to `CACHE_KEY_PREFIX` |

---

### Task 1: Add External Dependencies and Worktree Dev Guide to ARCHITECTURE.md

**Files:**
- Modify: `ARCHITECTURE.md`

- [ ] **Step 1: Add External Dependencies section after Module Map**

Add after the "Other" module table (around line 127):

```markdown
## External Dependencies

| Service | Used For | Required? |
|---------|----------|-----------|
| **PostgreSQL** | Conversation storage, policy config, auth config, request logs, telemetry config | Yes (or SQLite for local mode) |
| **Redis** | Credential validation cache, activity event pub/sub (SSE), policy config distributed lock, last-credential-type metadata | No — in-process replacements used when `REDIS_URL` is empty |

When `REDIS_URL` is unset, the gateway runs in **local mode**: single-process with in-process pub/sub (`InProcessEventPublisher`), in-process credential cache (`InProcessCredentialCache`), and no distributed policy lock. This is appropriate for single-user local development.

### Observability Pipeline

Events flow through a multi-sink emitter:

```
Application code
    |
    v
EventEmitter (observability/emitter.py)
    |-- stdout (structured logging)
    |-- Database (TransactionRecorder → conversation_events)
    |-- EventPublisher (activity SSE stream)
           |-- RedisEventPublisher (when Redis available)
           |-- InProcessEventPublisher (local mode)
                    |
                    v
              /activity/monitor SSE endpoint (ui/routes.py)
```

`EventEmitter` is the high-level multi-sink dispatcher. `EventPublisherProtocol` is the transport layer for the SSE activity stream specifically. Don't confuse the two — "Emitter" dispatches to multiple sinks, "Publisher" is one specific sink (the SSE activity feed).

### Configuration Surfaces

Settings live in different places depending on their nature:

| Surface | What goes here | Example |
|---------|---------------|---------|
| `Settings` (pydantic-settings, env vars) | Infrastructure config | `DATABASE_URL`, `REDIS_URL`, `GATEWAY_PORT`, `SENTRY_DSN` |
| YAML via `POLICY_CONFIG` | Policy selection and policy-specific config | Policy class, model, threshold |
| Admin API (`/api/admin/*`) | Runtime-changeable settings | Active policy, auth mode, gateway settings |
| CLI config (`~/.luthien/config.toml`) | Per-user CLI preferences | Repo path, default policy |
```

- [ ] **Step 2: Add Worktree Development section**

Add after the Configuration Surfaces table:

```markdown
## Development from Worktrees

Docker Compose does **not** work reliably from worktrees — volume mounts resolve relative to the main repo, not the worktree. Instead, run a local dev server directly:

```bash
DATABASE_URL=sqlite:///tmp/luthien-dev.db REDIS_URL="" \
  uv run uvicorn luthien_proxy.main:create_app --factory --port 8001
```

This starts the gateway in local mode (SQLite + in-process pub/sub). The activity monitor, admin API, and history UI all work. Add `tmp/` to your global gitignore to avoid committing SQLite files.

For e2e tests from a worktree, use `sqlite_e2e` markers which start an in-process gateway automatically:
```bash
uv run pytest -m sqlite_e2e tests/e2e_tests/ --no-cov
```
```

- [ ] **Step 3: Commit**

```bash
git add ARCHITECTURE.md
git commit -m "docs: add external deps, observability pipeline, and worktree dev guide to ARCHITECTURE.md"
```

---

### Task 2: Update CLAUDE.md with Worktree Pattern and Context Doc Warning

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add worktree dev server pattern to Development Workflow section**

Add after the "Local Docker Development" section:

```markdown
### Worktree Development (No Docker)

Docker Compose volume mounts resolve relative to the main repo root, not worktrees. To run a dev server from a worktree:

```bash
DATABASE_URL=sqlite:///tmp/luthien-dev.db REDIS_URL="" \
  uv run uvicorn luthien_proxy.main:create_app --factory --port 8001
```

This starts in local mode (SQLite, in-process pub/sub). All UI endpoints work. For e2e tests: `uv run pytest -m sqlite_e2e --no-cov`.
```

- [ ] **Step 2: Add context doc reliability warning**

Add to the "Maintaining Context" section, after the bullet list:

```markdown
**Reliability warning**: Files in `dev/context/` are written by both humans and AI agents. Agent-written content may contain inferences presented as facts. Before relying on a claim in these docs (especially around how authentication, streaming, or edge cases work), verify against the actual code. If you discover incorrect information, fix it immediately rather than working around it.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add worktree dev pattern and context doc reliability warning to CLAUDE.md"
```

---

### Task 3: Fix dev/context/authentication.md OAuth Section

**Files:**
- Modify: `dev/context/authentication.md`

- [ ] **Step 1: Update the OAuth section**

Replace lines 68-76 (the "OAuth Passthrough" section) with corrected information:

```markdown
## OAuth Passthrough (2026-03-18, updated 2026-03-18)

Claude Code authenticates via OAuth (Claude Pro/Max accounts). The transport header is authoritative for credential type:

- `Authorization: Bearer <token>` → OAuth token → `AnthropicClient(auth_token=token)`
- `x-api-key: <token>` → API key → `AnthropicClient(api_key=token)`

**Important**: The transport header (Bearer vs x-api-key) is the correct way to distinguish credential types. Do NOT use prefix-based detection (`sk-ant-*` inspection). This was verified empirically — Claude Code always uses the correct transport for each credential type (PR #347).

`is_anthropic_api_key(token)` still exists in `credential_manager.py` as tech debt. It should not be used for new code — use the transport header instead.
```

- [ ] **Step 2: Update the Incoming Request Auth Flow section**

Replace lines 32-33 (the passthrough detection logic) to remove prefix-based language:

```markdown
3. Otherwise (passthrough) → forward the client's token:
   - Received via `Authorization: Bearer` → `AnthropicClient(auth_token=token)` (OAuth token)
   - Received via `x-api-key` → `AnthropicClient(api_key=token)` (API key)
```

- [ ] **Step 3: Commit**

```bash
git add dev/context/authentication.md
git commit -m "docs: fix incorrect OAuth detection docs — transport header is authoritative, not prefix"
```

---

### Task 4: Add Reliability Warning to dev/context/README.md

**Files:**
- Modify: `dev/context/README.md`

- [ ] **Step 1: Add warning section**

Add after the "Adding New Context" section:

```markdown
## Reliability of Context Documents

These documents are written by both humans and AI agents during development. **Agent-written content may contain incorrect inferences presented as facts.** Known incidents:

- `authentication.md` documented prefix-based OAuth detection (`sk-ant-*`) as intentional architecture. It was actually a bug — the transport header (`Authorization: Bearer` vs `x-api-key`) is the correct discriminator. This incorrect doc caused a future agent to spend time defending the wrong approach.

**When reading these docs:**
- Treat claims about behavior as "probably true, verify before relying on"
- If something seems wrong, check the code — the code is authoritative
- When you fix incorrect information, note the correction with a timestamp
```

- [ ] **Step 2: Commit**

```bash
git add dev/context/README.md
git commit -m "docs: add reliability warning to context docs README"
```

---

### Task 5: Add Worktree/Docker and E2E Coverage Gotchas

**Files:**
- Modify: `dev/context/gotchas.md`

- [ ] **Step 1: Add worktree Docker gotcha**

Add at the end of the file:

```markdown
## Docker Compose Does Not Work From Worktrees (2026-03-18)

**Gotcha**: `docker-compose.yaml` volume mounts (`./src:/app/src:ro`) resolve relative to the main repo root, not the worktree directory. Running `docker compose up` from a worktree mounts the main repo's source code, not the worktree's.

- **Symptom**: Code changes in the worktree don't appear in the running container
- **Also**: `.env` is gitignored and only exists in the main repo root
- **Fix**: Use the local dev server instead: `DATABASE_URL=sqlite:///tmp/luthien-dev.db REDIS_URL="" uv run uvicorn luthien_proxy.main:create_app --factory --port 8001`
- **See also**: ARCHITECTURE.md "Development from Worktrees" section

## Coverage Output Drowns E2E Test Results (2026-03-18)

**Gotcha**: `pyproject.toml` defaults include `--cov` which produces a full coverage table. For e2e tests (which go through Docker), this table shows 0% on all source files and obscures actual test pass/fail output.

- **Symptom**: `uv run pytest -m mock_e2e ... 2>&1 | tail -30` shows nothing but coverage table
- **Fix**: Add `--no-cov` when running e2e, mock_e2e, or sqlite_e2e tests
- **Example**: `uv run pytest -m sqlite_e2e tests/e2e_tests/ --no-cov -v`
```

- [ ] **Step 2: Commit**

```bash
git add dev/context/gotchas.md
git commit -m "docs: add worktree/Docker and e2e coverage gotchas"
```

---

### Task 6: Remove Orphaned Test Suites

**Files:**
- Delete: `tests/unit_tests/test_overseer_llm.py`
- Delete: `tests/unit_tests/test_overseer_main.py`
- Delete: `tests/unit_tests/test_overseer_report_server.py`
- Delete: `tests/unit_tests/test_overseer_session_driver.py`
- Delete: `tests/unit_tests/test_overseer_stream_parser.py`
- Delete: `tests/unit_tests/test_saas_infra/` (entire directory)
- Modify: `pyproject.toml` (remove all `saas_infra` references)

- [ ] **Step 1: Verify these tests have no corresponding source**

```bash
grep -r "overseer" src/luthien_proxy/ --include="*.py" -l
# Should return nothing (luthien_cli has one reference but it's unrelated)
ls src/luthien_proxy/saas_infra/ 2>/dev/null
# Should not exist
```

- [ ] **Step 2: Delete orphaned test files**

```bash
rm tests/unit_tests/test_overseer_llm.py
rm tests/unit_tests/test_overseer_main.py
rm tests/unit_tests/test_overseer_report_server.py
rm tests/unit_tests/test_overseer_session_driver.py
rm tests/unit_tests/test_overseer_stream_parser.py
rm -rf tests/unit_tests/test_saas_infra/
```

- [ ] **Step 3: Remove all `saas_infra` references from pyproject.toml**

Three changes needed:
1. `[tool.ruff] src`: change `["src", "tests", "saas_infra"]` to `["src", "tests"]`
2. `[tool.ruff.lint] per-file-ignores`: remove the `"tests/unit_tests/test_saas_infra/**"` entry
3. `[tool.pyright] include`: change `["src", "saas_infra"]` to `["src"]`

- [ ] **Step 4: Run tests to confirm nothing broke**

```bash
uv run pytest tests/unit_tests/ -q
```

- [ ] **Step 5: Commit**

```bash
git add -A tests/unit_tests/test_overseer_* tests/unit_tests/test_saas_infra/ pyproject.toml
git commit -m "chore: remove orphaned overseer and saas_infra test suites"
```

---

### Task 7: Consolidate Duplicate Policy Core Test Directories

**Files:**
- Move: `tests/unit_tests/test_policy_core/*.py` → `tests/unit_tests/policy_core/`
- Delete: `tests/unit_tests/test_policy_core/`

- [ ] **Step 1: Move test files from test_policy_core/ to policy_core/**

Move only the test files — do NOT move `__init__.py` (the target directory already has one).

```bash
mv tests/unit_tests/test_policy_core/test_chunk_builders.py tests/unit_tests/policy_core/
mv tests/unit_tests/test_policy_core/test_policy_context.py tests/unit_tests/policy_core/
mv tests/unit_tests/test_policy_core/test_streaming_utils.py tests/unit_tests/policy_core/
rm -rf tests/unit_tests/test_policy_core/
```

- [ ] **Step 2: Run policy_core tests to verify**

```bash
uv run pytest tests/unit_tests/policy_core/ -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/unit_tests/policy_core/ tests/unit_tests/test_policy_core/
git commit -m "chore: consolidate duplicate policy_core test directories"
```

---

### Task 8: Rename REDIS_KEY_PREFIX to CACHE_KEY_PREFIX

**Files:**
- Modify: `src/luthien_proxy/credential_manager.py`

- [ ] **Step 1: Rename the constant**

In `credential_manager.py`, rename `REDIS_KEY_PREFIX` to `CACHE_KEY_PREFIX` (replace all occurrences). The constant is used 8 times in the file. The value stays the same — it's just the name that's misleading since the cache backend may be in-process, not Redis.

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/unit_tests/test_credential_manager.py -v
```

- [ ] **Step 3: Commit**

```bash
git add src/luthien_proxy/credential_manager.py
git commit -m "refactor: rename REDIS_KEY_PREFIX to CACHE_KEY_PREFIX in credential_manager"
```

---

### Task 9: Run dev_checks and Push

- [ ] **Step 1: Run full dev checks**

```bash
./scripts/dev_checks.sh
```

- [ ] **Step 2: Fix any issues**

- [ ] **Step 3: Push to origin**

```bash
git push -u origin HEAD
```
