# Plugin Header Contract

> **Version**: v1.0 (contract version, independent of plugin npm version)
> **Status**: Active (Track A)
> **Related**: [opencode-luthien plugin](https://github.com/LuthienResearch/opencode-luthien), [PR #758](https://github.com/LuthienResearch/luthien-proxy/pull/758) (gateway implementation)

This document defines the canonical set of HTTP headers injected by the `opencode-luthien` plugin into every proxied request. The gateway reads these headers to populate observability columns in `request_logs`.

---

## Trust Boundary

The gateway trusts `x-luthien-*` headers as received — it does not authenticate their origin beyond the client credential check already applied to every request. Operators who expose the gateway to untrusted clients should configure a reverse proxy to strip the specific headers documented here (`x-luthien-session-id`, `x-luthien-agent`, `x-luthien-provider`, `x-luthien-model`, `x-luthien-plugin-version`) before they reach the gateway, preventing clients from spoofing session IDs or agent names in logs.

> **Note**: `x-luthien-user-id` is a separate header controlled by the `TRUST_USER_ID_HEADER` gateway config. A blanket strip of all `x-luthien-*` headers would silently disable user attribution for operators who have intentionally enabled that setting. Strip only the headers listed above.

---

## Headers

### `x-luthien-session-id`

| Field | Value |
|---|---|
| **Source** | OpenCode session ID (UUIDv4, provided by OpenCode runtime) |
| **Type** | String (UUIDv4 format, max 36 chars, pattern `[0-9a-f-]{36}`) |
| **Required** | No — absent when plugin is not loaded or proxy is unreachable |
| **Semantics** | Identifies the OpenCode session that originated the request. Unique per OpenCode process invocation. Shared across all requests within a single session. |
| **Example** | `x-luthien-session-id: 550e8400-e29b-41d4-a716-446655440000` |
| **Persisted to** | `request_logs.session_id` (dedicated column) |

> **Validation**: The gateway stores the value as-is with no length enforcement or UUID validation. The plugin MUST send a valid UUIDv4 (max 36 chars). Behavior for malformed or oversized values is undefined until PR-B adds explicit validation.

### `x-luthien-agent`

| Field | Value |
|---|---|
| **Source** | OpenCode agent name (e.g., `build`, `test`, `review`) |
| **Type** | String (max 64 chars, printable ASCII) |
| **Required** | No |
| **Semantics** | Identifies which OpenCode agent mode was active when the request was made. Useful for filtering logs by agent type. When the header is absent, `request_logs.agent` is NULL. The plugin sends `"unknown"` when the agent name is unavailable. |
| **Example** | `x-luthien-agent: build` |
| **Persisted to** | `request_logs.agent` (dedicated column, introduced in PR-B / migration 019) |

### `x-luthien-provider`

| Field | Value |
|---|---|
| **Source** | Plugin — derived from the AI SDK provider ID |
| **Type** | String — known values: `anthropic`, `openai`, `google` |
| **Required** | No |
| **Semantics** | Identifies which AI provider the request targets. Redundant with the URL prefix (`/openai/`, `/gemini/`, `/anthropic/`) but included for convenience. Unknown values are logged via `request_headers` JSONB and passed through — the gateway does not reject unrecognised provider strings, supporting forward-compatibility as new providers are added. |
| **Example** | `x-luthien-provider: openai` |
| **Persisted to** | `request_logs.request_headers` JSONB (not a dedicated column — derivable from `endpoint` URL prefix at query time) |

### `x-luthien-model`

| Field | Value |
|---|---|
| **Source** | Plugin — derived from the AI SDK model ID |
| **Type** | String |
| **Required** | No |
| **Semantics** | Identifies the specific model requested (e.g., `gpt-4o`, `claude-3-5-sonnet-20241022`, `gemini-1.5-flash`). |
| **Example** | `x-luthien-model: gpt-4o` |
| **Persisted to** | `request_logs.model` (existing column) |

### `x-luthien-plugin-version`

| Field | Value |
|---|---|
| **Source** | Plugin — hardcoded to the plugin's npm package version |
| **Type** | String (semver) |
| **Required** | No |
| **Semantics** | Identifies the version of the `opencode-luthien` plugin that injected these headers. Useful for debugging version-specific behavior. |
| **Example** | `x-luthien-plugin-version: 0.1.0` |
| **Persisted to** | `request_logs.request_headers` JSONB (not a dedicated column) |

---

## Gateway Behavior

- **Inbound**: The gateway reads `x-luthien-session-id`, `x-luthien-agent`, and `x-luthien-model` from inbound requests and persists them to dedicated `request_logs` columns (agent column introduced in PR-B).
- **Outbound**: All `x-luthien-*` headers are **stripped** before forwarding to upstream providers (Anthropic, OpenAI, Gemini). They are internal observability headers and must not leak to external APIs.
- **Unknown headers**: Any `x-luthien-*` header not listed above is logged (via `request_headers` JSONB) and ignored. This supports additive evolution.
- **Missing headers**: When `x-luthien-*` headers are absent (plugin not loaded, proxy unreachable), the corresponding `request_logs` columns are NULL. Requests still succeed.

---

## Versioning Policy

This contract is at **v1.0**.

| Change type | Policy |
|---|---|
| **Add a new `x-luthien-*` header** | Allowed. Bump minor version (v1.0 -> v1.1). Gateway ignores unknown headers. |
| **Remove an existing header** | **FORBIDDEN**. Removing a header is a breaking change. If removal is required, deprecate first (one release cycle), then remove in a new major version. |
| **Change the semantics of an existing header** | **FORBIDDEN**. Create a new header with a new name instead. |
| **Change the format of an existing header** | Treat as a semantic change — **FORBIDDEN** without a new header name. |

The plugin sends `x-luthien-plugin-version` on every request so the gateway can detect version mismatches in logs.

---

## Related

- Plugin source: [LuthienResearch/opencode-luthien](https://github.com/LuthienResearch/opencode-luthien)
- Plugin README: see plugin repo for installation and configuration
- Gateway passthrough routes: `src/luthien_proxy/passthrough_routes.py` (introduced in PR-B)
- Database schema: `migrations/postgres/008_add_request_logs_table.sql` (session_id), `migrations/postgres/019_add_agent_to_request_logs.sql` (agent, introduced in PR-B)
- Track B: Native provider pipelines will replace the passthrough routes and may extend this contract
