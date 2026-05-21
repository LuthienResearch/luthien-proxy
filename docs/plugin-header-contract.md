# Plugin Header Contract

> **Version**: v1.0
> **Status**: Active (Track A)
> **Related**: [opencode-luthien plugin](https://github.com/LuthienResearch/opencode-luthien), [Track A plan](.sisyphus/plans/track-a-opencode-luthien-bridge.md)

This document defines the canonical set of HTTP headers injected by the `opencode-luthien` plugin into every proxied request. The gateway reads these headers to populate observability columns in `request_logs`.

---

## Headers

### `x-luthien-session-id`

| Field | Value |
|---|---|
| **Source** | OpenCode session ID (UUIDv4, provided by OpenCode runtime) |
| **Type** | String (UUID format) |
| **Required** | No — absent when plugin is not loaded or proxy is unreachable |
| **Semantics** | Identifies the OpenCode session that originated the request. Unique per OpenCode process invocation. Shared across all requests within a single session. |
| **Example** | `x-luthien-session-id: 550e8400-e29b-41d4-a716-446655440000` |
| **Persisted to** | `request_logs.session_id` (dedicated column) |

### `x-luthien-agent`

| Field | Value |
|---|---|
| **Source** | OpenCode agent name (e.g., `build`, `test`, `review`) |
| **Type** | String |
| **Required** | No — defaults to `"unknown"` when agent name is unavailable |
| **Semantics** | Identifies which OpenCode agent mode was active when the request was made. Useful for filtering logs by agent type. |
| **Example** | `x-luthien-agent: build` |
| **Persisted to** | `request_logs.agent` (dedicated column, added by migration 018) |

### `x-luthien-provider`

| Field | Value |
|---|---|
| **Source** | Plugin — derived from the AI SDK provider ID |
| **Type** | String (one of: `anthropic`, `openai`, `google`) |
| **Required** | No |
| **Semantics** | Identifies which AI provider the request targets. Redundant with the URL prefix (`/openai/`, `/gemini/`, `/anthropic/`) but included for convenience. |
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

- **Inbound**: The gateway reads `x-luthien-session-id`, `x-luthien-agent`, and `x-luthien-model` from inbound requests and persists them to dedicated `request_logs` columns.
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
- Gateway passthrough routes: `src/luthien_proxy/passthrough_routes.py` (Track A bridge code)
- Database schema: `migrations/postgres/008_add_request_logs_table.sql` (session_id), `migrations/postgres/018_add_agent_to_request_logs.sql` (agent)
- Track B: Native provider pipelines will replace the passthrough routes and may extend this contract
