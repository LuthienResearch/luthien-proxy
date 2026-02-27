# Gotchas

Non-obvious behaviors, edge cases, and things that are easy to get wrong.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Testing (2025-10-08, updated 2025-10-24)

- E2E tests (`pytest -m e2e`) are SLOW - use sparingly, prefer unit tests for rapid iteration
- Always run `./scripts/dev_checks.sh` before committing - formats, lints, type-checks, and tests
- **OTel disabled in tests**: Set `OTEL_ENABLED=false` in test environment to avoid connection errors to Tempo endpoint. Module-level `tracer = trace.get_tracer()` calls trigger OTel initialization at import time.
- **LiteLLM type warnings**: When working with `ModelResponse`, use proper typed objects (`Choices`, `StreamingChoices`, `Message`, `Delta`) to avoid Pydantic serialization warnings from LiteLLM's `Union` types. See test fixtures for examples.
- **E2E tests**: E2E tests remain (test_gateway_matrix.py, test_streaming_chunk_structure.py).

## Docker Development (2025-10-08, updated 2026-02-03)

- **Shell env overrides .env for API keys**: Docker Compose's `${VAR}` syntax checks shell environment FIRST, `.env` file second. If `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set in your shell (e.g., from `~/.zshrc`), it will override `.env` values. The fix is to use `env_file: .env` in docker-compose.yaml and NOT list API keys in the `environment:` block.
- **Env changes require recreate, not restart**: `docker compose restart gateway` updates the code, but does NOT reload `.env` changes - it reuses the existing container config. Use `docker compose up -d gateway` to recreate with updated env vars.
- Check logs with `docker compose logs -f gateway` when debugging
- Long-running compose or `uv` commands can hang the CLI; launch them via `scripts/run_bg_command.sh` so you can poll logs (`tail -f`) and terminate with the recorded PID if needed.
- **Services**: gateway, db, and redis

## Observability Checks (2025-10-08, updated 2025-11-11)

- Uses OpenTelemetry for observability - see `dev/observability.md` and `dev/VIEWING_TRACES_GUIDE.md`
- Live activity monitoring available at `/activity/monitor` on the gateway

## Documentation Structure (2025-10-10, updated 2025-11-11)

- **Active docs**: dev/REQUEST_PROCESSING_ARCHITECTURE.md, dev/observability.md, dev/VIEWING_TRACES_GUIDE.md
- **Common places to check**: README.md, CLAUDE.md, dev planning docs, inline code comments
- **Streaming behavior**: Emits conversation events via `storage/events.py` using background queue for non-blocking persistence

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

## Image/Multimodal Handling Through Proxy (2025-12-15)

**Gotcha**: Images pass validation after PR #104 fix, but Claude may respond to wrong image content.

- **Fixed (PR #104)**: Validation error - changed `Request.messages` to `list[dict[str, Any]]`, added image block conversion
- **Still broken**: Claude sometimes describes wrong image - suspect LiteLLM→Anthropic conversion issue
- **Tracking**: Issue #108 has full troubleshooting logs

## Thinking Blocks Must Come First in Anthropic Responses (2026-01-14)

- Anthropic API requires `thinking`/`redacted_thinking` blocks BEFORE text content
- LiteLLM exposes these via `message.thinking_blocks` (list of dicts)
- Wrong order causes: `Expected 'thinking' or 'redacted_thinking', but found 'text'`

## Thinking Blocks in Multi-Turn Conversations (2026-01-24)

**Gotcha**: Extended thinking requires fixes at THREE layers - streaming, format conversion, AND request validation.

1. **Streaming assembler** must recognize `reasoning_content` and `thinking_blocks` from LiteLLM
2. **Format conversion** (`anthropic_to_openai_request`) must preserve thinking blocks in message history - they were silently dropped!
3. **Pydantic validation** must allow list content in `AssistantMessage` - OpenAI types don't natively support thinking blocks

**Symptoms by layer**:
- Layer 1 missing: Single-turn works, but no thinking content visible
- Layer 2 missing: 500 error from Anthropic: `"Expected 'thinking' or 'redacted_thinking', but found 'text'"`
- Layer 3 missing: 400 error from proxy: `"AssistantMessage.content: Input should be a valid string"`

**Files involved**: `anthropic_sse_assembler.py`, `llm_format_utils.py`, `types/anthropic.py`, `types/openai.py`

## LiteLLM Delivers Thinking Signatures Out of Order (2026-01-24)

**Gotcha**: LiteLLM sends `signature_delta` AFTER text content starts, but Anthropic requires it BEFORE the thinking block closes.

**Expected order** (Anthropic native):
1. thinking_deltas → 2. signature_delta → 3. content_block_stop → 4. text starts

**LiteLLM actual order**:
1. thinking_deltas → 2. text starts → 3. signature_delta (too late!)

**Fix**: Delay `content_block_stop` for thinking blocks until signature arrives. Track `thinking_block_needs_close` flag and `last_thinking_block_index`.

## LiteLLM >= 1.81.0 Breaking Changes in Streaming (2026-01-29)

**Gotcha**: litellm 1.81.0+ introduced breaking changes in streaming response handling.

**Breaking changes**:
1. `StreamingChoices.delta` returns a `dict` instead of a `Delta` object
2. `finish_reason` defaults to `"stop"` instead of `None` for intermediate chunks
3. `StreamingChoices` passed to `ModelResponse` may get converted to `Choices`

**Fix**: Use `response_normalizer.py` to normalize all streaming chunks:
- `normalize_chunk()` - converts dict deltas to `Delta` objects
- `normalize_chunk_with_finish_reason()` - also restores intended `finish_reason`
- `normalize_stream()` - wraps async streams to normalize each chunk

**Where normalization happens**:
- `litellm_client.py` calls `normalize_stream()` on raw litellm output
- Downstream code should receive already-normalized chunks

**Test helpers**: Use `litellm_test_utils.make_streaming_chunk()` to create normalized chunks for tests.

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

1. **Check the raw request/response**: Use `/debug/calls/{call_id}` endpoint to see original vs transformed payloads
2. **Compare streaming chunks**: Enable debug logging to see each SSE chunk as it flows through pipeline
3. **Isolate the layer**: Is it request conversion? Response assembly? Policy transformation?
4. **Reproduce minimally**: Strip the request down to simplest failing case
5. **Check LiteLLM version**: Breaking changes in LiteLLM streaming have caused issues (see litellm >= 1.81.0 gotcha)

**Tools available**:
- Activity monitor: `/activity/monitor` - live SSE stream
- Debug diff viewer: `/debug/diff?call_id=X` - before/after comparison
- Tempo traces: Search by `luthien.call_id` attribute via `http://localhost:3200/api/search`

## Policy Config Dynamic Loading Security (2026-02-04)

**Gotcha**: `POLICY_CONFIG` allows loading arbitrary Python classes at runtime. This is intentional for flexibility but has security implications.

**Security considerations**:
- Only trusted users should have access to modify `policy_config.yaml` or set `POLICY_CONFIG` env var
- Policy classes are instantiated with full Python capabilities
- In production: ensure config files have proper filesystem permissions
- The Admin API requires `ADMIN_API_KEY` authentication for runtime policy changes

**Related TODO item**: Add security documentation for dynamic policy loading.

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
- **Regression coverage**: `tests/unit_tests/orchestration/test_policy_orchestrator_request.py` includes in-place mutation tests for both request and response recording

## Policy Instances Are Frozen After Configuration (2026-02-27)

**Gotcha**: Policies are now frozen after instantiation via `_instantiate_policy(...)`. Any attempt to assign `self.some_attr = ...` during request handling raises immediately.

- **Symptom**: Runtime `AttributeError` mentioning policy is frozen
- **Cause**: Storing request/streaming state on policy instance instead of request context
- **Correct pattern**: Use `PolicyContext` typed `StateSlot[T]` state for per-request mutable data
- **Additional guard**: Mutable container attrs on policy objects are rejected during instantiation

---

(Add gotchas as discovered with timestamps: YYYY-MM-DD)
