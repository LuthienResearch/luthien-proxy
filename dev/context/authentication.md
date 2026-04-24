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

Headers are stored lowercase. The gateway extracts the bearer token in `verify_token()` and forwards it as `context.user_credential` in `passthrough`/`both` auth modes.

---

## Judge LLM Key Resolution (2026-03-17, updated 2026-04-23)

Both `SimpleLLMPolicy` and `ToolCallJudgePolicy` issue a separate judge-model call per decision. Judge calls are routed through `luthien_proxy.llm.judge_client.judge_completion`, which wraps `litellm.acompletion` — LiteLLM is intentionally scoped to this judge path only and is not on the main request pipeline.

Each judge policy's YAML config declares a required `auth_provider` field. The gateway's `CredentialManager.resolve(provider, context)` turns that declaration into a concrete credential at call time:

- **`"user_credentials"`** — forward the client's incoming credential (`context.user_credential`, populated by the gateway from `Authorization: Bearer` or `x-api-key` in passthrough/both modes). Fails with `CredentialError` if no user credential is on the request. This is the default behavior shipped in `config/policy_config.yaml` and the bundled presets.
- **`{"server_key": "<name>"}`** — look up an operator-provisioned key stored in the `server_credentials` DB table.
- **`{"user_then_server": "<name>"}`** — try user credentials first, fall back to the named server key (with `on_fallback` = `"fallback"` / `"warn"` / `"fail"`).

The resolved `Credential` is passed as `api_key=` kwarg to `judge_completion()`. There is no env-var fallback — `LLM_JUDGE_API_KEY` and `LITELLM_MASTER_KEY` were removed in PR #603.

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
| `src/luthien_proxy/credentials/auth_provider.py` | `AuthProvider` types (`UserCredentials`, `ServerKey`, `UserThenServer`) and `parse_auth_provider()` |
| `src/luthien_proxy/credential_manager.py` | `CredentialManager.resolve(provider, context)` — turns a policy's `auth_provider` into a `Credential` |
| `src/luthien_proxy/policies/simple_llm_policy.py` | Declares `auth_provider` in `SimpleLLMJudgeConfig`; resolves via `CredentialManager` before each judge call |
| `src/luthien_proxy/policies/tool_call_judge_policy.py` | Declares `auth_provider` in `ToolCallJudgeConfig`; resolves via `CredentialManager` before each judge call |
| `src/luthien_proxy/types.py` | `RawHttpRequest` — stores headers for policy access |
