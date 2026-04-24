# `claude -p` Investigation Notes (PR #2, inference-provider-abstraction)

These notes document the experimentally-verified mechanism by which the
proxy's `ClaudeCodeProvider` authenticates a *specific, operator-provisioned*
OAuth access token to the `claude` Claude Code CLI, plus related observations
(latency, flags, output shape). This is load-bearing documentation for PR #3
(provider registry) and PR #4 (policy YAML rename + user-passthrough dispatch).

Environment tested: Claude Code v2.1.119 on Ubuntu 6.8.0 (`cavil`), April 2026.

---

## 1. Is `claude -p` available?

Yes.

```
$ which claude
/home/jai/.local/bin/claude
$ claude --version
2.1.119 (Claude Code)
```

## 2. How does `claude` discover credentials by default?

Reads `~/.claude/.credentials.json` (mode `0600`). Shape:

```json
{"claudeAiOauth": {"accessToken": "sk-ant-oat01-…", "refreshToken": "sk-ant-ort01-…", …}}
```

The `sk-ant-oat01-` prefix distinguishes an OAuth access token from a raw
API key (`sk-ant-api03-…`). Claude Code sessions mint subscription-backed
OAuth tokens via `claude auth`. In the non-`--bare` default mode, the CLI
also consults the OS keychain and the user's home dir for `CLAUDE.md`,
skills, hooks, etc.

## 3. Env / flag levers we tested

| Lever | Behavior | Useful? |
|---|---|---|
| `ANTHROPIC_API_KEY=<oat-token>` + `--bare` | **Works.** Subprocess authenticates with the injected OAuth access token. No keychain/credentials.json read. | **YES — this is the mechanism we use.** |
| `ANTHROPIC_API_KEY=<api-key>` + `--bare` | Works (API key path). Standard billed-API behavior. | Only useful if operator wants to back a `ClaudeCodeProvider` with an API key instead of OAuth — but `DirectApiProvider` is cheaper for that case. Not our primary use. |
| `CLAUDE_CONFIG_DIR=<scratch>` (no `--bare`) | CLI reads `<scratch>/.credentials.json`. Works if we materialize a credentials.json there. | Works, but leaks to disk and still pulls in the full (heavy) Claude Code context — 19–50k input tokens per call. Rejected. |
| `CLAUDE_CONFIG_DIR=<scratch>` + `--bare` | Bare ignores `.credentials.json` entirely. Prints `Not logged in · Please run /login`. | Not useful — bare mode explicitly doesn't read credentials files. |
| Bare mode alone (no API key env var) | `Not logged in · Please run /login` | Expected — bare is strict about auth. |
| `--settings` with `apiKeyHelper` | Documented alternative in `--bare` help. | Would require materializing a helper script per call; env-var path is strictly simpler. Not used. |

`--bare` is documented as "skip hooks, LSP, plugin sync, attribution,
auto-memory, background prefetches, keychain reads, and CLAUDE.md
auto-discovery. Sets CLAUDE_CODE_SIMPLE=1. Anthropic auth is strictly
ANTHROPIC_API_KEY or apiKeyHelper via --settings (OAuth and keychain are
never read)." Bare mode also **never reads `~/.claude/.credentials.json`**.

## 4. Chosen credential-presentation mechanism

`ClaudeCodeProvider` invocation uses:

- `claude -p --bare --output-format json [--model MODEL] [--system-prompt PROMPT]`
- `env` overrides (we do NOT inherit the full parent env):
  - `PATH` — inherited, so `claude` can find node/etc.
  - `HOME=<scratch_dir>` — isolates from the operator's real home.
  - `CLAUDE_CONFIG_DIR=<scratch_dir>` — belt-and-suspenders isolation.
  - `ANTHROPIC_API_KEY=<credential.value>` — the OAuth access token.
- **No files on disk.** No temp credentials.json, no settings file.
- Prompt is passed as the positional argument to avoid stdin plumbing.

The scratch dir is `tempfile.mkdtemp()` per-provider-instance; it stays empty
because `--bare` doesn't read from it. We keep it for isolation clarity.

## 5. Output shape (`--output-format json`)

```json
{
  "type":"result","subtype":"success","is_error":false,
  "api_error_status":null,
  "duration_ms":4102,"duration_api_ms":2210,"num_turns":1,
  "result":"pong",
  "stop_reason":"end_turn",
  "session_id":"…",
  "total_cost_usd":0.31237375,
  "usage":{"input_tokens":6,"cache_creation_input_tokens":49951,…},
  "modelUsage":{"claude-opus-4-7[1m]":{…}},
  "terminal_reason":"completed",
  "fast_mode_state":"off",
  "uuid":"…"
}
```

We extract `.result` as the assistant message. On failure the server still
emits a JSON object with `is_error:true`, `api_error_status` (e.g. `401`),
and a human-readable `result` like `"Invalid API key · Fix external API
key"`. The provider translates these into `InferenceError` subclasses.

Invalid token example:

```json
{"type":"result","subtype":"success","is_error":true,"api_error_status":401,
 "duration_ms":218,"result":"Invalid API key · Fix external API key",…}
```

## 6. Cold-spawn latency

Measured on cavil (desktop, warm disk cache, warm Anthropic network):

| Mode | Wall-clock | Input tokens (cache-create) | Notes |
|---|---|---|---|
| default (`claude -p …`) | **~8.1 s** | 49,951 | Full CLAUDE.md + skills + hooks sync. Unusable for judges. |
| `CLAUDE_CONFIG_DIR=scratch` (no bare) | ~3.6 s | 19,192 | Still heavy. |
| `--bare` + OAuth env | **~2.2 s** | 1,719 | Our chosen path. |

Conclusions:
- Cold spawn of `claude -p --bare` is **≈2 s of wall time**, dominated by
  node startup + one API roundtrip. The minimum-prompt cost is ~1.7k input
  tokens of bare-mode infrastructure per call.
- This is too slow to be sub-200ms-competitive with a raw HTTP call, but
  it's well within the envelope for a judge policy that runs every few
  requests or on-demand.
- Process pooling / warm workers would help (keep one `claude` session
  alive per credential) but adds complexity. **Not in scope for PR #2.**
  Flag for later: if latency becomes a pain point, investigate whether
  `claude`'s `stream-json` input mode can act like a persistent REPL.

## 7. Supported flags we rely on

- `-p, --print` — non-interactive mode (required for subprocess use).
- `--bare` — strict auth + strip extras (required).
- `--output-format json` — machine-parseable single-object output.
- `--model <name>` — e.g. `claude-sonnet-4-6`, `claude-opus-4-7`. Defaults
  to sonnet in bare mode. Unknown models produce a clean error.
- `--system-prompt <str>` — overrides the default system prompt.
- `--max-budget-usd <amount>` — works with `-p`. Available but we don't
  wire it through in PR #2.

Not available / not used:

- `--max-tokens` — not a CLI flag in 2.1.119. Token limits are only
  controllable via model choice or by configuring the session. **The
  `max_tokens` parameter in `InferenceProvider.complete()` is accepted
  but silently ignored by `ClaudeCodeProvider` with a debug-level log
  line. Documented in the provider docstring.**
- `--temperature` — also not a CLI flag. Same treatment as `max_tokens`.
- Multi-turn `messages` via stdin — only works with
  `--input-format=stream-json --output-format=stream-json` which changes
  the entire output contract. For PR #2 we render
  `messages: list[dict[str, str]]` into a single concatenated prompt string
  (system role → `--system-prompt`, everything else → the positional prompt
  with role markers). Tradeoff documented in the provider.

## 8. credential_override semantics for ClaudeCodeProvider

We intentionally raise `InferenceCredentialOverrideUnsupported` (a subclass of
`InferenceError`) when `credential_override` is set. User-supplied Anthropic
credentials (API keys or user OAuth tokens) don't meaningfully authenticate
the `claude -p` binary against the *operator's* Claude subscription — that's
the whole reason for having a `DirectApiProvider` alternative.

PR #4 will add higher-level dispatch logic that catches this error and falls
back to the corresponding `DirectApiProvider` when a policy is configured for
`user_then_provider`. We keep that dispatch OUT of the provider itself to
keep `ClaudeCodeProvider`'s contract narrow.

## 9. Quirks / gotchas

- Bare mode reports `total_cost_usd` based on standard per-token pricing
  even when the underlying auth is a subscription OAuth token (which
  doesn't bill per call on Anthropic's side). Treat it as an
  *informational* usage hint, not an authoritative dollar figure.
- The default model in `--bare` is `claude-sonnet-4-6`, not opus. Always
  pass `--model` explicitly when the caller specifies one.
- `claude -p` always emits exactly one JSON object on stdout in
  `--output-format json` mode, followed by a newline. stderr is empty on
  success; we capture and surface stderr in error messages for visibility.
- Non-zero exit code does NOT always accompany `is_error:true` — the CLI
  can exit 0 with an error result body (e.g. 401). Rely on the JSON body,
  not the exit code, for success/failure classification. We still capture
  non-zero exit codes and classify them as `InferenceError`.

## 10. Structured output via `--json-schema` (verified)

The `claude` CLI exposes structured output via the `--json-schema <json>`
flag (visible in `claude --help`; the truncated `claude -p --help` snapshot
in an earlier session happened to clip the relevant lines). Works with
`--bare` + OAuth. Full help text:

```
--json-schema <schema>   JSON Schema for structured output validation.
                         Example: {"type":"object","properties":{...},"required":[...]}
```

### Invocation (verified)

```bash
claude -p --bare --output-format json \
  --json-schema '{"type":"object","properties":{"city":{"type":"string"},"population":{"type":"integer"}},"required":["city","population"],"additionalProperties":false}' \
  "Return ONLY a JSON object matching the schema for Paris, France. Just JSON."
```

Latency: ~4–13 s wall on cold-spawn (higher variance than plain mode;
model may burn an extra turn or tool call to validate). Not a regression
for judge use; flagged for future benchmarking.

### Envelope shape (verified)

**Success with structured output:**

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "api_error_status": null,
  "result": "",                              // usually empty string in this mode
  "structured_output": {"city":"Paris","population":2161000},
  "stop_reason": "end_turn",
  "terminal_reason": "completed",
  "num_turns": 1,
  ...
}
```

**Model declined the schema (wrote prose instead):**

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "result": "Waves fold into shore,\nsalt and silence trade secrets — ...",
  "structured_output": null,
  ...
}
```

This is a *surprise*: the CLI does NOT mark `is_error:true` when the
model ignores the schema; it silently sets `structured_output` to `null`
and puts free-form text in `result`. Our provider treats this as
`InferenceStructuredOutputError` because the caller asked for structured
output and didn't get it — preserving the interface contract predictably.

**Retry exhaustion:**

```json
{"subtype": "error_max_structured_output_retries", "is_error": true, ...}
```

Verified the literal string `"error_max_structured_output_retries"`
exists in the shipped `claude` binary (2.1.119) via `strings | grep`.
We did not manage to trigger it organically in testing — the model
complied or declined cleanly with every schema we threw at it, including
an impossible-regex `^IMPOSSIBLE_PREFIX_[A-Z]{99999}$`. Likely only
appears when the Anthropic service-level retry count for structured
output is exceeded mid-generation. The provider handles it from the
envelope subtype, not from observation.

### Failure modes we observed but do NOT translate

- **Malformed `--json-schema` argument** (non-JSON string, or JSON that
  isn't a valid JSON Schema): the CLI hangs indefinitely with no output,
  no exit, no stderr. Neither `strings $(which claude)` nor a quick
  timeout revealed a graceful error path. Mitigation: our
  `timeout_seconds` (default 120s) eventually raises
  `InferenceTimeoutError`. Schemas built by our callers are generally
  static so this should be rare, but it's worth documenting for PR #3/#4.

### Why `DirectApiProvider` does post-hoc validation instead

LiteLLM accepts `{"type": "json_object"}` uniformly across providers but
its `{"type": "json_schema", ...}` variant has subtly different wrapper
shapes per provider (Anthropic vs OpenAI vs Vertex). Rather than carry
that matrix, our direct-API path sends a `json_object` hint + a
schema-blurb in the system prompt, and validates with `jsonschema`
post-hoc. Failures map to the same `InferenceStructuredOutputError`
class as the CLI path, so callers branch once.
