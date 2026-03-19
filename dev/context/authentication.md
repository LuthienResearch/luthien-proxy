# Authentication Architecture

How auth works end-to-end, including passthrough auth and judge key resolution.

---

## Auth Modes (2026-03-17)

Configured via `AUTH_MODE` env var / `auth_config` DB table. Three modes:

- **`proxy_key`**: Client must present `PROXY_API_KEY`. Server uses its own `ANTHROPIC_API_KEY` for backend calls.
- **`passthrough`**: Client's own Anthropic API key (or OAuth token) is forwarded directly to the Anthropic backend. No proxy key needed.
- **`both`** (default): Accepts either the proxy key or a passthrough credential. If the token matches `PROXY_API_KEY`, uses server's client; otherwise treats it as a passthrough credential.

Managed by `CredentialManager` (`src/luthien_proxy/credential_manager.py`). Auth config is persisted to DB (`auth_config` table) and exposed via admin API (`/api/admin/auth/config`).

---

## Incoming Request Auth Flow (2026-03-17)

**Entry point**: `verify_token()` in `gateway_routes.py`

Token is extracted from, in order:
1. `Authorization: Bearer <token>` header
2. `x-api-key: <token>` header

**For Anthropic (`/v1/messages`)**: `resolve_anthropic_client()` then decides which `AnthropicClient` to use:

1. `x-anthropic-api-key` header → always use that key directly (explicit override)
2. Token matches `PROXY_API_KEY` and mode is not `passthrough` → use server's `AnthropicClient` (configured via `ANTHROPIC_API_KEY` env var)
3. Otherwise (passthrough) → forward the client's token:
   - Bearer + NOT an `sk-ant-*` API key → `AnthropicClient(auth_token=token)` (OAuth token)
   - `sk-ant-*` API key (any transport) → `AnthropicClient(api_key=token)`

**For Anthropic (`/v1/messages`)**: `verify_token()` validates but the token is NOT forwarded to LiteLLM — LiteLLM uses env vars (`ANTHROPIC_API_KEY`, etc.) or per-request `api_key` kwargs.

---

## Passthrough Key in PolicyContext (2026-03-17)

The raw incoming token is available to policies via:

```python
context.raw_http_request.headers.get("authorization")  # "Bearer sk-ant-..."
context.raw_http_request.headers.get("x-api-key")      # "sk-ant-..."
```

Headers are stored lowercase. `BasePolicy._extract_passthrough_key(raw_http_request)` handles extraction (checks `authorization` Bearer first, then `x-api-key`).

---

## Judge LLM Key Resolution (2026-03-17)

Both `SimpleLLMPolicy` and `ToolCallJudgePolicy` make separate LiteLLM calls to a judge model. Key priority (resolved per-request at call time):

1. **Explicit policy config `api_key`** — set by admin in the policy config. Highest priority, overrides everything.
2. **Passthrough key** — the client's incoming API key from `context.raw_http_request.headers`. Default behavior: the client's key is used for judge calls too.
3. **`LLM_JUDGE_API_KEY` env var** — server-configured key specifically for judge calls.
4. **`LITELLM_MASTER_KEY` env var** — legacy catch-all fallback.
5. **None** — LiteLLM resolves via its own env var chain (`ANTHROPIC_API_KEY`, etc.).

This is implemented in `_resolve_api_key(context)` on each policy class (backed by `BasePolicy._extract_passthrough_key()`). The resolved key is passed as `api_key=` kwarg to `call_simple_llm_judge()` / `call_judge()`.

**Why passthrough-first**: In the common case where the client authenticates with their own Anthropic key, judge calls should use the same key — no extra server configuration needed. Admins who need to use a different key for the judge (e.g., a shared org key) can set `LLM_JUDGE_API_KEY` or the per-policy `api_key`.

---

## OAuth Passthrough (2026-03-18)

Claude Code authenticates via OAuth (Claude Pro/Max accounts). The OAuth bearer token is forwarded to Anthropic via `AnthropicClient(auth_token=token)`.

`is_anthropic_api_key(token)` in `credential_manager.py` distinguishes `sk-ant-*` API keys from OAuth tokens. API keys are always sent via `api_key=` (x-api-key header), while non-`sk-ant-*` bearer tokens use `auth_token=` (Authorization: Bearer).

**Known tech debt**: The prefix-based distinction (`sk-ant-*`) is a workaround. Ideally, credential type should be surfaced by the credential manager during validation so the gateway can treat each credential type without string inspection.

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
