---
category: Breaking Changes
pr: TBD
---

**Rename `PROXY_API_KEY` → `CLIENT_API_KEY` and reframe auth docs around passthrough as the default.** The gateway's client-facing story is now: Luthien is just an Anthropic endpoint — clients set `ANTHROPIC_BASE_URL` and use their normal `ANTHROPIC_API_KEY` (or Claude Pro/Max OAuth). There is no Luthien-specific key on the client side. The old `PROXY_API_KEY` name leaked a gateway-internal concept into operator docs and confused the mental model; the config field, env var, `AuthMode.PROXY_KEY` → `AuthMode.CLIENT_KEY`, and the `"proxy_key"` auth mode string are all renamed consistently. Observability credential-type markers are also renamed for clarity (`"client_api_key"` → `"user_api_key"`, `"proxy_key_fallback"` → `"client_key_match"`). A new migration (013) rewrites any existing `auth_config.auth_mode = 'proxy_key'` rows to `'client_key'`. Docs across `README.md`, `dev-README.md`, `dev/context/authentication.md`, `docs/standalone-container.md`, `deploy/README.md`, and `src/luthien_cli/README.md` are rewritten to lead with passthrough as the typical default and present `CLIENT_API_KEY` as an optional operator-side feature.

**Deployment notes for operators:**

- **Environment:** existing deployments with `PROXY_API_KEY` / `AUTH_MODE=proxy_key` in their environment must rename to `CLIENT_API_KEY` / `AUTH_MODE=client_key` before restart.
- **Postgres migration ordering:** migration 013 rewrites the stored `auth_config.auth_mode` value. SQLite deployments migrate in-process and are safe. On Postgres, migrations run in a separate service — ensure migration 013 has applied before restarting the gateway image. As a safety net, the gateway now *tolerates* a legacy `'proxy_key'` row at startup: it logs a warning and treats it as `'client_key'` so the service stays up until the migration runs. This tolerance is temporary and will be removed in a follow-up release.
- **Observability badge:** any in-flight `last_credential_info` entries with the pre-rename marker strings (`"client_api_key"`, `"proxy_key_fallback"`) are silently ignored by the updated nav badge. The badge reflects the new markers as soon as new traffic flows.
