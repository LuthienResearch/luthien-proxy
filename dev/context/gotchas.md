# Gotchas

Non-obvious behaviors, edge cases, and things that are easy to get wrong.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Testing (2025-10-08, updated 2026-04-10)

- E2E tests (`pytest -m e2e`) are SLOW - use sparingly, prefer unit tests for rapid iteration
- Always run `./scripts/dev_checks.sh` before committing - formats, lints, type-checks, and tests
- **OTel disabled in tests**: Set `OTEL_ENABLED=false` in test environment to avoid connection errors to Tempo endpoint. Module-level `tracer = trace.get_tracer()` calls trigger OTel initialization at import time.
- **LiteLLM type warnings (judge policies only)**: `judge_client.judge_completion` and the judge-side utilities in `simple_llm_utils.py` / `tool_call_judge_utils.py` still call `litellm.acompletion` and receive `ModelResponse`. When constructing judge-call fixtures, use proper typed objects (`Choices`, `Message`) to avoid Pydantic serialization warnings. The main gateway request path is Anthropic-SDK-only and does not touch these types.

## Docker Development (2025-10-08, updated 2026-02-03)

- **Shell env overrides .env for API keys**: Docker Compose's `${VAR}` syntax checks shell environment FIRST, `.env` file second. If `ANTHROPIC_API_KEY` is set in your shell (e.g., from `~/.zshrc`), it will override `.env` values. The fix is to use `env_file: .env` in docker-compose.yaml and NOT list API keys in the `environment:` block.
- **Env changes require recreate, not restart**: `docker compose restart gateway` updates the code, but does NOT reload `.env` changes - it reuses the existing container config. Use `docker compose up -d gateway` to recreate with updated env vars.
- Check logs with `docker compose logs -f gateway` when debugging
- Long-running compose or `uv` commands can hang the CLI; launch them via `scripts/run_bg_command.sh` so you can poll logs (`tail -f`) and terminate with the recorded PID if needed.
- **Services**: gateway, db, and redis

## Observability Checks (2025-10-08, updated 2025-11-11)

- Uses OpenTelemetry for observability - see `dev/observability.md` and `dev/VIEWING_TRACES_GUIDE.md`
- Live activity monitoring available at `/history` on the gateway

## Documentation Structure (2025-10-10, updated 2026-04-10)

- **Start with code**: For anything load-bearing, read the module in `src/luthien_proxy/` — architecture documents lag.
- **Canonical docs**: `ARCHITECTURE.md` (has known staleness tracked on Trello), `dev-README.md`, inline docstrings.
- **Common places to check**: README.md, CLAUDE.md, inline code comments.
- **Streaming behavior**: Emits conversation events via `storage/events.py` using a background queue for non-blocking persistence.

## Queue Shutdown for Stream Termination (2025-01-20, updated 2025-10-20)

**Gotcha**: Use `asyncio.Queue.shutdown()` for stream termination, not `None` sentinel values

- **Why**: Python 3.11+ built-in queue shutdown raises `QueueShutDown` when drained - cleaner than sentinel patterns
- **Wrong**: Putting `None` (gets consumed/lost during batch draining)
- **Right**: Call `queue.shutdown()` to signal end; catch `QueueShutDown` exception
- **Batch processing**: Block with `await queue.get()` for first item (don't busy-wait with `get_nowait()` in loop!)
- **Why it matters**: Busy-wait consumes 100% CPU; proper blocking is essential for async efficiency

## Anthropic SSE Requires Stateful Block Index Tracking (2025-11-03)

- OpenAI chunks lack block indices; Anthropic clients need sequential indices (0,1,2...) + lifecycle events (start/delta/stop)
- Use `AnthropicSSEAssembler` to maintain state across chunks (llm/anthropic_sse_assembler.py)

## Policies Must Forward Content Chunks (2025-11-04)

- If policy handles tool calls but doesn't implement `on_content_delta()`, content never reaches client
- Always forward content chunks in `on_content_delta()` (see tool_call_judge_policy.py:146)
- Use `Delta(content=text)` not `{"content": text}` - dicts break SSE assembly
- Use `StreamingChoices` not `Choices` for streaming (utils.py create_text_chunk)

## finish_reason Must Be in Separate Final Chunk for Tool Calls (2025-11-21)

**Gotcha**: Multiple tool calls streamed with `finish_reason` on each chunk causes clients to interpret them as separate responses.

- **Wrong**: `create_tool_call_chunk(tool, finish_reason="tool_calls")` for each tool
- **Right**: `create_tool_call_chunk(tool)` for each tool, then `create_finish_chunk("tool_calls")` once at end in `on_stream_complete()`

See `SimplePolicy.on_stream_complete()` for the pattern.

## Claude Code --tools Flag Restricts Available Tools (2025-11-24)

**Gotcha**: Using `--tools "Bash(echo:*)"` or similar permission patterns in Claude Code's `-p` mode unexpectedly restricts the tool list to only MCP tools, removing built-in tools like `Bash`, `Read`, `Edit`.

- **Wrong**: `claude -p --tools "Bash(echo:*)" "run echo hello"` - Bash tool not available
- **Right**: `claude -p "run echo hello"` - uses default tool set including Bash
- **Why**: The `--tools` flag with permission patterns appears to filter the tool list differently than expected. When testing Claude Code through the gateway, omit `--tools` to get the full default tool set.

## Image/Multimodal Handling Through Proxy (2025-12-15, note 2026-04-10)

**Gotcha**: Images pass validation after PR #104 fix, but Claude may respond to wrong image content.

- **Fixed (PR #104)**: Validation error - changed `Request.messages` to `list[dict[str, Any]]`, added image block conversion
- **Still broken**: Claude sometimes describes wrong image
- **Tracking**: Issue #108 has full troubleshooting logs
- **Stale caveat**: The original note attributed this to a "LiteLLM→Anthropic conversion issue" from an older architecture where the gateway went through LiteLLM. The Anthropic path no longer uses LiteLLM, so the root cause needs to be re-investigated if this still reproduces.

## Extended Thinking Blocks (2026-01-14, superseded 2026-04-10)

**Historical only.** Earlier notes in this section described thinking-block handling from the era when the gateway ran backend calls through LiteLLM and had to untangle `thinking_blocks` / `reasoning_content` / `signature_delta` ordering, along with referenced modules (`anthropic_sse_assembler.py`, `response_normalizer.py`, `litellm_client.py`, `litellm_test_utils.py`) that no longer exist. The current Anthropic path uses the Anthropic SDK directly and consumes `MessageStreamEvent` values (from `anthropic.lib.streaming`) unmodified, so those workarounds do not apply. If thinking-block ordering bugs resurface, re-investigate against `pipeline/anthropic_processor.py` and the SDK event types rather than the old LiteLLM behavior.

Still relevant from that era: the **Anthropic API invariant** that all content-block events must complete before `message_delta` (see "Anthropic Streaming: All Content Blocks Must Precede message_delta" below) and the general requirement that `thinking` / `redacted_thinking` blocks appear before plain text in an assistant message.

## Content and finish_reason Must Be in Separate Chunks (2026-01-30)

**Gotcha**: The SSE assembler's `convert_chunk_to_event()` returns early when there's text content, never reaching the `finish_reason` check. Creating a chunk with BOTH content AND finish_reason causes the finish_reason to be silently ignored.

**Symptoms**:
- Claude Code shows blank responses with "No assistant message found" errors
- SSE stream missing `content_block_stop` and `message_delta` events
- `message_stop` appears but without proper block closure

**Wrong**: Creating a single chunk with content + finish_reason
```python
chunk = create_text_chunk(text=transformed, finish_reason=stream_state.finish_reason)
ctx.push_chunk(chunk)  # finish_reason is ignored!
```

**Right**: Emit content first, then finish_reason in a separate chunk
```python
content_chunk = create_text_chunk(text=transformed, finish_reason=None)
ctx.push_chunk(content_chunk)

if stream_state.finish_reason:
    finish_chunk = create_finish_chunk(finish_reason=stream_state.finish_reason)
    ctx.push_chunk(finish_chunk)
```

**Why**: `convert_chunk_to_event()` checks content before finish_reason. If content exists, it returns a `text_delta` event immediately. The finish_reason check never runs.

**Affected policies**: Any policy that buffers content and emits it in `on_content_complete()`. See `StringReplacementPolicy` for the correct pattern.

## E2E Test Debugging: Direct API vs Proxy (2026-02-04)

**Gotcha**: When an E2E test fails through the proxy but works with direct API calls, the issue is in the proxy's format conversion or streaming pipeline—not the test itself.

**Debugging pattern**:
1. Run the same request directly against Anthropic/OpenAI API (bypassing proxy)
2. If direct API works but proxy fails: problem is in proxy's request conversion or response assembly
3. Compare raw SSE output from both endpoints to find divergence
4. Common culprits: thinking blocks ordering, tool call chunk assembly, finish_reason handling

**Example**: `test_anthropic_client_openai_backend_preserves_anthropic_format` - model uses tools correctly via direct API but not through proxy → indicates request conversion drops tool instructions or response doesn't preserve tool call format.

## Debugging Proxy Issues: Jai's Checklist (2026-02-04)

**Context**: Common debugging steps for proxy issues (from co-founder debugging sessions).

1. **Check the raw request/response**: Use `/api/debug/calls/{call_id}` endpoint to see original vs transformed payloads
2. **Compare streaming chunks**: Enable debug logging to see each SSE chunk as it flows through pipeline
3. **Isolate the layer**: Is it request conversion? Response assembly? Policy transformation?
4. **Reproduce minimally**: Strip the request down to simplest failing case
5. **Check the Anthropic SDK version**: Gateway request processing is SDK-direct. If upstream stream events change shape, `pipeline/anthropic_processor.py` is where the breakage will surface. LiteLLM is only used inside judge policies and is isolated from the main request path.

**Tools available**:
- History/Conversation view: `/history` - conversation sessions and live details
- Debug diff viewer: `/diffs?call_id=X` - before/after comparison
- Tempo traces: Search by `luthien.call_id` attribute via `http://localhost:3200/api/search`

## Policy Config Dynamic Loading Security (2026-02-04)

**Gotcha**: `POLICY_CONFIG` allows loading arbitrary Python classes at runtime. This is intentional for flexibility but has security implications.

**Security considerations**:
- Only trusted users should have access to modify `policy_config.yaml` or set `POLICY_CONFIG` env var
- Policy classes are instantiated with full Python capabilities
- In production: ensure config files have proper filesystem permissions
- The Admin API requires `ADMIN_API_KEY` authentication for runtime policy changes from non-localhost clients (localhost is bypassed by default via `LOCALHOST_AUTH_BYPASS=true`; set to `false` to enforce on loopback — see the reverse-proxy gotcha below)

**Related TODO item**: Add security documentation for dynamic policy loading.

## Admin Auth Localhost Bypass + Same-Host Reverse Proxy (2026-04-10)

**Gotcha**: `LOCALHOST_AUTH_BYPASS=true` (the default) skips admin auth for any request whose TCP source IP matches loopback. `is_localhost_request()` in `src/luthien_proxy/auth.py:30-35` inspects `request.client.host` only — it does NOT parse `X-Forwarded-For` or any other forwarding header. That means:

- If Luthien runs behind a reverse proxy on the **same host** (Caddy, nginx, Traefik, Tailscale Funnel, cavil.jai.one's Caddy), every forwarded request appears as `127.0.0.1` to the gateway.
- The admin API (`/api/admin/*`) and the monitoring/policy dashboards are then effectively unauthenticated to the public internet.
- The Luthien gateway request scheme *does* honor `X-Forwarded-Proto` (`auth.py:132-142`) — so the header-trust story is asymmetric: scheme is trusted from headers, source IP is not.

**Fix for operators**:
- For any same-host reverse-proxy deployment: set `LOCALHOST_AUTH_BYPASS=false` in the gateway env.
- Railway sets it to `false` at startup automatically (`src/luthien_proxy/main.py:607-609`) if the variable isn't explicitly set, so cloud deployments via `deploy/railway.json` are safe out of the box.

**Structural fix tracked at**: https://trello.com/c/6IspgIkX — the code should probably either parse trusted forwarding headers, or auto-disable the bypass when forwarding headers are present.

**Related to**: the admin auth docs correction (PR #531) — the previous docs claimed admin endpoints always required the bearer token, masking this landmine.

## Docker Compose Orphaned Containers from Mismatched Project Names (2026-02-17)

**Gotcha**: `quick_start.sh` sets `COMPOSE_PROJECT_NAME=luthien-<dirname>`, but running `docker compose up` directly uses just the directory name as the project. `docker compose down` only stops containers for the current project name, leaving old containers bound to ports.

- **Symptom**: `Bind for 0.0.0.0:6379 failed: port is already allocated` on startup
- **Cause**: Orphaned containers from a previous run with a different project name
- **Fix**: `quick_start.sh` now cleans up containers from the default project name before starting. Additionally, `COMPOSE_PROJECT_NAME=luthien-proxy` is now set in `.env.example` so all launch methods use the same project name by default (PR #231).
- **Manual fix**: `docker compose -p <directory-name> down` (e.g., `docker compose -p luthien-proxy down`)
## macOS Bash 3 Compatibility (2026-02-17)

**Gotcha**: macOS ships with bash 3.2, which does NOT support `declare -A` (associative arrays), `(( ))` C-style for loops, or `${!var}` indirect expansion in all contexts. Scripts using `#!/bin/bash` will use the system bash 3.

- **Wrong**: `declare -A MAP=([key]=value)` — fails on macOS
- **Right**: Use parallel simple arrays, positional parameters (`set --`), or `eval` for indirect variable access
- **Affected**: `scripts/find-available-ports.sh`

## asyncpg JSONB Columns Can Return str or dict (2026-02-19)

**Gotcha**: asyncpg may return JSONB columns as either `dict` or `str`, depending on connection settings and PostgreSQL version. Code that assumes `isinstance(payload, dict)` will silently drop str payloads.

- **Wrong**: `dict(row["payload"]) if isinstance(row["payload"], dict) else {}` — silently discards str payloads
- **Wrong**: Removing the `isinstance(str)` branch as "dead code" — it's not dead, asyncpg genuinely returns str sometimes
- **Right**: Handle both cases explicitly with `json.loads()` for str, `dict()` for dict, `TypeError` for anything else
- **Affected files**: `history/service.py`, `debug/service.py`
- **Discovered during**: Codebase cleanup PR #211 — services-impl teammate removed the str branch thinking it was defensive dead code, causing 7 e2e test failures
## PaaS PORT vs GATEWAY_PORT (2026-02-19)

**Gotcha**: Railway (and Heroku, Render, etc.) inject `PORT` at runtime. The app reads `GATEWAY_PORT`. If you set `GATEWAY_PORT` to empty string in the PaaS dashboard, pydantic crashes trying to parse `""` as `int` and the app dies before serving `/health`.

- **Bridge**: `start-gateway.sh` maps `PORT → GATEWAY_PORT` so deploys work without manual env var config
- **Why not just use PORT?**: `GATEWAY_PORT` is more descriptive and consistent with the rest of the settings. `PORT` is ambiguous in multi-service setups.
- **Why not set GATEWAY_PORT=${{PORT}} in Railway dashboard?**: That's what was tried originally — it was set to empty string and broke every deploy. The shell bridge is less fragile.

## Dogfooding Safety: Agent Can Kill Its Own Proxy (2026-02-25)

**Gotcha**: When dogfooding Luthien (running Claude Code through the proxy on the same machine), the agent can accidentally kill the proxy by running Docker commands like `docker compose down`. This severs the agent's own API connection — session-ending, unrecoverable.

- **Symptom**: "API Error: Unable to connect to API (ConnectionRefused)" — agent is dead, can't even explain what happened
- **Cause**: No safety layer prevents self-destructive infrastructure commands. The active policy (e.g., DeSlop/StringReplacementPolicy) only processes text — tool calls pass through unexamined.
- **Workaround**: Never run Docker restart/stop commands from a proxied Claude Code session. Use a separate terminal.
- **Architectural fix needed**: A "system policy" layer that always runs regardless of user-configured policy, blocking commands that would kill the proxy.
- **Related**: PR #203 (orphaned containers — previous Docker self-interference incident)
- **Dangerous commands to watch for**: `docker compose down`, `docker stop`, `docker compose restart` (on gateway), `kill` on gateway PID, `docker compose exec db` (DB access)

## In-Place Policy Mutation Can Hide Live-View Diffs (2026-02-27)

**Gotcha**: If the orchestrator records "original" request/response objects *after* policy hooks run, in-place policy mutation destroys the pre-policy snapshot and `request_was_modified` / `response_was_modified` become false even when content changed.

- **Symptom**: Live view shows transformed output (e.g., ALL CAPS) but `original_*_messages` are missing and `*_was_modified=false`
- **Cause**: Passing mutable objects directly to `record_request()` / `record_response()` without deep-copying before hook execution
- **Fix**: In `PolicyOrchestrator`, snapshot with `model_copy(deep=True)` before calling policy hooks, then record `(original_snapshot, final_object)`
- **Regression coverage**: `tests/luthien_proxy/unit_tests/orchestration/test_policy_orchestrator_request.py` includes in-place mutation tests for both request and response recording

## Policy Config Validation Flags Public Mutable Attrs (2026-02-27)

**Gotcha**: Policy loading runs a lightweight validation step via `_instantiate_policy(...)` that rejects public mutable container attrs.

- **Symptom**: Policy load fails with `TypeError` about a mutable container attr
- **Cause**: Public policy attrs should represent immutable config; mutable request data belongs in request context
- **Correct pattern**: Keep request-scoped mutable data in `PolicyContext.get_request_state()` / `pop_request_state()`
- **Scope**: This is load-time validation only (no runtime instance freezing)

## Passthrough Auth Mode Is DB-Persisted, Not Just Env (2026-02-27)

**Gotcha**: `AUTH_MODE` env var is only the startup default; the effective mode is loaded from `auth_config` in DB on startup. If DB says `proxy_key`, passthrough tests will skip/fail even when container env has `AUTH_MODE=both`.

- **Symptom**: Passthrough tests skip with "Gateway not in passthrough/both auth mode" while env shows `AUTH_MODE=both`
- **Fix**: Set mode explicitly via admin API: `POST /admin/auth/config {"auth_mode":"both"}`

**Related gotcha**: The transport header is authoritative for credential type — `Authorization: Bearer` = OAuth, `x-api-key` = API key. The gateway does not inspect token prefixes (`sk-ant-api...`). In practice, clients always use the correct transport for their credential type (verified with real Claude Code traffic, PR #347).

## Claude Code Internal Probe Requests Use max_tokens=1 (2026-03-05)

**Gotcha**: Claude Code sends internal probe requests (token counting, quota checks) with `max_tokens=1`. These get recorded as `transaction.request_recorded` events. If not filtered, the probe's content (e.g., "quota", "count") becomes the session title in /history.

- **Symptom**: Session titled "quota" or "count" instead of the actual first user message
- **Detection**: Both Python (`_extract_preview_message`) and SQL (`session_first_message` CTE) filter requests where `max_tokens <= 1`. This is a structural signal — no real conversation uses `max_tokens=1`.
- **Why not a content blocklist?**: The original fix (PR #133) used a blocklist of probe words. This required manual updates for each new probe. The structural `max_tokens` check catches all probes regardless of content.
- **If this breaks again**: A probe with `max_tokens > 1` would bypass the filter. Check the actual `max_tokens` value in the DB for the offending request before changing the approach.

## Docker Compose Stale Images Skip New Migrations (2026-03-06)

**Gotcha**: `docker compose up -d` without `--build` reuses cached images. If migration files were added after the last image build, the migrations container won't have them and won't apply them. The gateway's Python-side migration check (`check_migrations`) won't catch this either — it compares `/app/migrations/` (baked into the equally stale gateway image) against the DB, so both sides are missing the same files and the check passes.

- **Symptom**: 500 errors like `relation "request_logs" does not exist` after adding migration files
- **Cause**: Migrations container built before new `.sql` files existed; `up` without `--build` reuses old image
- **Fix**: `quick_start.sh` now uses `--build` on all `docker compose up` calls (PR #299)
- **Manual fix**: `docker compose build migrations gateway` then `docker compose up -d`

## Mock Compose Needs Env Var Port Allocation (2026-03-18)

**Gotcha**: `docker-compose.mock.yaml` uses `network_mode: host`, so the gateway binds directly to host ports. `GATEWAY_PORT`, `POSTGRES_PORT`, and `REDIS_PORT` must be passed through to the container's environment block — Docker port mappings are ignored under host networking.

- **Symptom**: `[Errno 98] address already in use` on port 8000 when another service is running
- **Fix**: Override `GATEWAY_PORT` in mock yaml env block; always `source scripts/find-available-ports.sh` before starting
- **Also**: `DATABASE_URL` and `REDIS_URL` must use `${POSTGRES_PORT}` / `${REDIS_PORT}` variables, not hardcoded ports

## pkgutil.walk_packages Needs Full Prefix for Recursion (2026-03-18)

**Gotcha**: `pkgutil.walk_packages(path, prefix="")` does NOT recurse into subpackages. It lists subpackage entries (`ispkg=True`) but doesn't walk their contents.

- **Wrong**: `walk_packages(pkg.__path__, prefix="")` — yields `presets` but not `presets.prefer_uv`
- **Right**: `walk_packages(pkg.__path__, prefix="luthien_proxy.policies.")` — yields full dotted names including subpackage modules
- **Affected**: `policy_discovery.py` — needed for preset policies in `policies/presets/` subpackage

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
- **Example**: `uv run pytest -m sqlite_e2e tests/luthien_proxy/e2e_tests/ --no-cov -v`

## Anthropic Streaming: All Content Blocks Must Precede message_delta (2026-03-18)

**Gotcha**: Anthropic's streaming protocol requires all content block events (`content_block_start`, `content_block_delta`, `content_block_stop`) to complete BEFORE `message_delta` is emitted. Injecting content blocks after `message_delta` corrupts client-side conversation history and permanently bricks the session (400 errors on every subsequent request).

- **Invariant**: `content_block_*` events → `message_delta` → `message_stop` (never content blocks after `message_delta`)
- **Wrong**: Injecting warning/error content blocks at `RawMessageStopEvent` (which fires after `message_delta`)
- **Right**: Inject content blocks at `RawMessageDeltaEvent`, *before* emitting the delta event itself
- **Symptom**: `API Error: 400` on the next request in the session, repeating forever. Only recovery is `/rewind`.
- **Why it's subtle**: `message_stop` feels like the "last event" and a natural injection point — but `message_delta` (which carries `stop_reason` and `usage`) already closed the content block window.
- **Pattern**: This is the 2nd instance of this bug class in 2 months (1st was PR #134 — thinking block signatures out of order, 2nd was PR #356 — warning injection after `message_delta`). Both were discovered by humans hitting them in real usage.
- **Architectural gap**: No pipeline-level validator enforces event ordering — each policy must get it right independently.

## Claude Code Preflight Calls Share Session ID with Real Turns (2026-04-03)

**Gotcha**: Claude Code sends multiple non-conversational API calls per session that share the same `session_id` as real conversation turns:
1. **Preflight/quota check**: `max_tokens=1`, no system prompt, non-streaming, single "quota" message
2. **Title generation**: `output_config` with `json_schema` format, has system prompt with title generation instructions, `tools=[]`

Policies using session-level state trackers (like `ConversationLinkPolicy`'s once-per-session injection tracker) can be silently consumed by these invisible calls, causing the actual first real turn to miss the intended behavior.

**Stateless per-request checks** (like `is_first_turn()` — checks for 1 user message, 0 assistant messages) are unaffected because they re-evaluate independently for each call.

**Fix**: Use `is_first_turn()` instead of session-level trackers for first-turn injection. Future improvement: implement turn-type classification utility (see Trello card "Add turn-type classification utility for policies") so policies can skip ALL non-conversational turns, not just first-turn behavior.

---

## Conversation Viewer Dedup Assumes Stable Cumulative History (2026-04-08)

**Gotcha**: The conversation viewer deduplicates messages by slicing each turn's `request_messages` array based on the previous turn's message count. This assumes the API always sends a stable, strictly-growing cumulative array.

- **Invariant**: `turn[N].request_messages[0..K]` is identical to `turn[N-1].request_messages[0..K]` where K is the previous turn's length.
- **Breaks when**: A policy rewrites earlier messages (different content at same positions), or the conversation is retried/resumed from a checkpoint (count resets or changes).
- **Failure mode**: Wrong messages shown in wrong turns — silently, with no error. A `console.warn` fires only when the slice produces *empty* results, not when it produces *wrong* results.
- **Mitigation**: Preflight turns (probes, title gen) are excluded from the running count so they don't corrupt the sequence.

## Fingerprint Source Must Match Between Initial Load and Refresh (2026-04-08)

**Gotcha**: The conversation viewer fingerprints turns to detect changes and avoid unnecessary DOM re-renders. The fingerprint source must be consistent between initial load (`renderTurns`) and SSE-triggered refresh (`refreshTurns`).

- **Bug found**: Initial load fingerprinted *presented* turns (with `_isPreflight`, `_displayMessages` fields), while refresh fingerprinted *raw* server turns. These always differed, causing full re-renders on every SSE event.
- **Fix**: Both paths now fingerprint raw server data (`_rawTurns`), not derived presentation state.
- **Coupling**: `rawTurns[i]` and `newTurns[i]` are aligned because `presentTurns()` maps 1:1 without filtering. If `presentTurns` ever filters turns, the index alignment breaks silently. A dev assertion guards this.

## request_params Blocklist Leaks Unknown Fields to Browser (2026-04-08)

**Gotcha**: When passing request parameters to the conversation viewer frontend, a blocklist approach (`if k not in ("messages", "system")`) forwards every field not explicitly excluded — including `metadata`, credentials, and any future fields added to the request pipeline.

- **Symptom**: Sensitive data visible in browser dev tools via the session detail API response
- **Fix**: Switched to an allowlist (`_REQUEST_PARAM_ALLOWLIST`). Only explicitly approved fields reach the frontend.
- **Also**: `output_config` is sanitized to only pass `format.type`, not the full JSON schema body.

---

(Add gotchas as discovered with timestamps: YYYY-MM-DD)
