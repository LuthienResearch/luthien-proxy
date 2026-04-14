# Authentication Architecture

How auth works end-to-end, including passthrough auth and judge key resolution.

---

## Client-Facing Mental Model (2026-04-10)

**From a client's perspective, the gateway is just an Anthropic endpoint.** Clients set `ANTHROPIC_BASE_URL` to the gateway and send their normal `ANTHROPIC_API_KEY` (or Claude Pro/Max OAuth token). There is no Luthien-specific credential, header, or flow on the client side. The SDK, Claude Code, `curl`, etc. all work unchanged.

Passthrough is the typically assumed default — the gateway forwards the client's own credentials upstream to Anthropic, so billing stays on the client's account.

---

## Auth Modes (2026-04-10)

Configured via `AUTH_MODE` env var / `auth_config` DB table. Three modes:

- **`passthrough`**: The client's own Anthropic API key or OAuth token is forwarded directly to the Anthropic backend. The server-side `ANTHROPIC_API_KEY` and `CLIENT_API_KEY` are not consulted for forwarding (the server key may still be used by judge policies — see below).
- **`client_key`**: Requests must present exactly the value set in `CLIENT_API_KEY`. The gateway then calls Anthropic using the server's own `ANTHROPIC_API_KEY`. If `ANTHROPIC_API_KEY` is unset in this mode, `/v1/messages` returns 500 (there are no credentials to forward upstream).
- **`both`** (default, set by `luthien onboard`): If the incoming token matches `CLIENT_API_KEY`, the gateway uses the server's client (requires `ANTHROPIC_API_KEY`). Otherwise the token is treated as a passthrough credential and forwarded as-is.

Managed by `CredentialManager` (`src/luthien_proxy/credential_manager.py`). Auth config is persisted to DB (`auth_config` table) and exposed via admin API (`/api/admin/auth/config`).

`CLIENT_API_KEY` is a gateway-side concept only. It's the value the operator configures as "accept this as if it were a real Anthropic credential." Clients that use it set it as their `ANTHROPIC_API_KEY` — they don't need to know it's special.

---

## Incoming Request Auth Flow (2026-04-10)

**Entry point**: `verify_token()` in `gateway_routes.py`

Token is extracted from, in order:
1. `Authorization: Bearer <token>` header
2. `x-api-key: <token>` header

**For Anthropic (`/v1/messages`)**: `resolve_anthropic_client()` then decides which `AnthropicClient` to use:

1. `x-anthropic-api-key` header → always use that key directly (explicit override)
2. Token matches `CLIENT_API_KEY` and mode is not `passthrough` → use server's `AnthropicClient` (configured via `ANTHROPIC_API_KEY` env var)
3. Otherwise (passthrough) → forward the client's token:
   - Received via `Authorization: Bearer` → `AnthropicClient(auth_token=token)` (OAuth token)
   - Received via `x-api-key` → `AnthropicClient(api_key=token)` (API key)

---

## Passthrough Key in PolicyContext (2026-03-17)

The raw incoming token is available to policies via:

```python
context.raw_http_request.headers.get("authorization")  # "Bearer sk-ant-..."
context.raw_http_request.headers.get("x-api-key")      # "sk-ant-..."
```

Headers are stored lowercase. `BasePolicy._extract_passthrough_key(raw_http_request)` handles extraction (checks `authorization` Bearer first, then `x-api-key`).

---

## Judge LLM Key Resolution (2026-03-17, updated 2026-04-10)

Both `SimpleLLMPolicy` and `ToolCallJudgePolicy` issue a separate judge-model call per decision. Judge calls are routed through `luthien_proxy.llm.judge_client.judge_completion`, which wraps `litellm.acompletion` — LiteLLM is intentionally scoped to this judge path only and is not on the main request pipeline.

Key priority (resolved per-request at call time):

1. **Explicit policy config `api_key`** — set by admin in the policy config. Highest priority, overrides everything.
2. **Passthrough key** — the client's incoming API key from `context.raw_http_request.headers`. Default behavior: the client's key is used for judge calls too.
3. **`LLM_JUDGE_API_KEY` env var** — server-configured key specifically for judge calls.
4. **`LITELLM_MASTER_KEY` env var** — legacy catch-all fallback (still read by the settings object).
5. **None** — LiteLLM resolves via its own env var chain (`ANTHROPIC_API_KEY`, etc.).

This is implemented in `_resolve_api_key(context)` on each policy class (backed by `BasePolicy._extract_passthrough_key()`). The resolved key is passed as `api_key=` kwarg to `call_simple_llm_judge()` / `call_judge()`.

**Why passthrough-first**: In the common case where the client authenticates with their own Anthropic key, judge calls should use the same key — no extra server configuration needed. Admins who need to use a different key for the judge (e.g., a shared org key) can set `LLM_JUDGE_API_KEY` or the per-policy `api_key`.

---

## OAuth Passthrough (2026-03-18)

Claude Code authenticates via OAuth (Claude Pro/Max accounts). The transport header is authoritative for credential type:

- `Authorization: Bearer <token>` → OAuth token → `AnthropicClient(auth_token=token)`
- `x-api-key: <token>` → API key → `AnthropicClient(api_key=token)`

**Important**: The transport header (Bearer vs x-api-key) is the correct way to distinguish credential types. Do NOT use prefix-based detection (`sk-ant-*` inspection). This was verified empirically — Claude Code always uses the correct transport for each credential type (PR #347).

---

## Relevant Files

| File | Role |
|------|------|
| `src/luthien_proxy/gateway_routes.py` | `verify_token()`, `resolve_anthropic_client()` |
| `src/luthien_proxy/credential_manager.py` | `CredentialManager`, auth mode config, credential validation/caching |
| `src/luthien_proxy/llm/anthropic_client.py` | `AnthropicClient` — wraps `AsyncAnthropic`, handles api_key vs auth_token |
| `src/luthien_proxy/policy_core/base_policy.py` | `_extract_passthrough_key()` |
| `src/luthien_proxy/policies/simple_llm_policy.py` | `_resolve_api_key()` for judge calls |
| `src/luthien_proxy/policies/tool_call_judge_policy.py` | `_resolve_api_key()` for judge calls |
| `src/luthien_proxy/types.py` | `RawHttpRequest` — stores headers for policy access |
