# Credential Management Standardization

**Status**: Steps 3-5a implemented (PR #496)
**Date**: 2026-03-31
**Updated**: 2026-04-01 (consistency pass after 3 rounds of adversarial review)

## Problem

Credential handling is scattered across the codebase with inconsistent patterns:

1. **Gateway routes** (`gateway_routes.py`): Extracts credentials from `Authorization: Bearer` and `x-api-key` headers in two parallel `Depends()` chains (`verify_token` and `resolve_anthropic_client`). Decides between passthrough and proxy-key modes, creates `AnthropicClient` instances with `api_key` or `auth_token` kwargs.

2. **Base policy** (`base_policy.py`): `_extract_passthrough_key` re-parses credentials from raw HTTP headers (a third extraction point). `_resolve_judge_api_key` implements a priority chain (explicit key → passthrough → server fallback). Both return raw strings with no type information.

3. **Judge utils** (`tool_call_judge_utils.py`, `simple_llm_utils.py`): Accept an `api_key: str | None` parameter, pass it as `api_key` kwarg to `litellm.acompletion()`. No awareness of whether the credential is an API key or OAuth token.

4. **Client cache** (`anthropic_client_cache.py`): Accepts `auth_type: Literal["api_key", "auth_token"]` alongside the raw credential string. The type information is ad-hoc — callers determine it independently.

5. **CredentialManager** (`credential_manager.py`): Handles validation caching with `is_bearer: bool` to choose the right transport header for validation calls. Manages auth modes but doesn't produce a typed credential object.

**Result**: Credential type is determined in multiple places using different heuristics. There's no single type that carries a credential + its metadata through the system.

## Design Overview

Introduce a uniform `Credential` value object and a single extraction point via a shared FastAPI dependency. `CredentialManager` becomes the single service for all credential operations: validation, auth mode enforcement, server credential storage, and auth provider resolution.

```
Request arrives
    ↓
get_request_credential() — extracts Credential from headers
    ↓ returns Credential
verify_token(credential) — validates against proxy key / CredentialManager
    ↓ returns validated Credential
    ├── resolve_anthropic_client(credential): builds AnthropicClient
    └── anthropic_messages(credential): sets on PolicyContext
            ↓
        Policy needs credential → CredentialManager.resolve(auth_provider, context)
            ↓
        Returns Credential (from context, server store, or fallback chain)
            ↓
        judge_completion() translates Credential → LiteLLM kwargs
```

This is a linear dependency chain: `resolve_anthropic_client` → `verify_token` → `get_request_credential`. Each function runs once because it appears once in the chain — not because of FastAPI dedup magic.

## Detailed Design

### 1. Credential Type

```python
# src/luthien_proxy/credentials/credential.py

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class CredentialType(str, Enum):
    """Credential type — matches AnthropicClient's auth_type vocabulary."""
    API_KEY = "api_key"
    AUTH_TOKEN = "auth_token"


@dataclass(frozen=True)
class Credential:
    """A credential that can authenticate against an LLM provider.

    Type-agnostic until the HTTP client layer, which inspects
    credential_type to set the right headers.
    """

    value: str
    credential_type: CredentialType
    platform: str         # "anthropic", "openai"
    platform_url: str | None = None
    expiry: datetime | None = None
```

`CredentialType` is a `str, Enum` so it serializes cleanly to YAML/JSON and matches the existing `auth_type: Literal["api_key", "auth_token"]` vocabulary in `anthropic_client_cache.py`. No new string constants — one vocabulary everywhere (code, DB, config).

The transport header is authoritative for `credential_type`:
- `Authorization: Bearer` → `CredentialType.AUTH_TOKEN`
- `x-api-key` → `CredentialType.API_KEY`

No prefix-based heuristics (`sk-ant-api`, `sk-ant-oat`, etc.). `is_anthropic_api_key()` is removed.

`platform` defaults to `"anthropic"` for now (non-Anthropic models are being deprecated). When multi-provider returns, the resolver will enforce platform compatibility.

### 2. Single Extraction Point (FastAPI Dependency)

```python
# In gateway_routes.py

async def get_request_credential(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> Credential:
    """Extract credential from request headers. Runs once per request.

    This is the bottom of the dependency chain — verify_token depends on it,
    and resolve_anthropic_client depends on verify_token. Each runs once
    because of the linear chain, not FastAPI dedup.
    """
    bearer_token = credentials.credentials if credentials else None
    api_key_header = request.headers.get("x-api-key")
    token = bearer_token or api_key_header
    if not token:
        raise HTTPException(status_code=401, detail="Missing API key")

    is_bearer = bearer_token is not None
    return Credential(
        value=token,
        credential_type=CredentialType.AUTH_TOKEN if is_bearer else CredentialType.API_KEY,
        platform="anthropic",
    )


async def verify_token(
    credential: Credential = Depends(get_request_credential),
    api_key: str | None = Depends(get_api_key),
    credential_manager: CredentialManager | None = Depends(get_credential_manager),
) -> Credential:
    """Validate the extracted credential. Returns it if valid."""
    # ... validation logic using credential.value, credential.credential_type ...
    return credential


async def resolve_anthropic_client(
    request: Request,
    credential: Credential = Depends(verify_token),
    base_client: AnthropicClient | None = Depends(get_anthropic_client),
) -> tuple[AnthropicClient, Credential]:
    """Build an AnthropicClient from the validated credential.

    Returns both the client and the credential that should be set on
    PolicyContext. These may differ if x-anthropic-api-key is present.
    """
    # x-anthropic-api-key overrides the forwarding credential only.
    # The auth credential (from get_request_credential) was used for
    # validation. The override key is a separate identity for the backend.
    override_key = request.headers.get("x-anthropic-api-key")
    if override_key:
        forwarding_cred = Credential(
            value=override_key,
            credential_type=CredentialType.API_KEY,
            platform="anthropic",
        )
    else:
        forwarding_cred = credential

    client = await anthropic_client_cache.get_client(
        forwarding_cred.value,
        auth_type=forwarding_cred.credential_type.value,
        base_url=base_url,
    )
    # Return forwarding_cred so the gateway can set it on PolicyContext.
    # Policies calling resolve(UserCredentials()) get the credential that
    # the backend actually sees, not the auth credential.
    return client, forwarding_cred
```

**`x-anthropic-api-key` handling**: This header lets a client authenticate with one credential (e.g., proxy key) but forward requests with a different one. The auth credential validates the client's access; the override key is what the backend sees. `PolicyContext.user_credential` is set to the **forwarding** credential (the override if present, otherwise the auth credential), because that's what policies care about — the identity making the backend request.

This replaces the current pattern where both `verify_token()` and `resolve_anthropic_client()` independently extract credentials from headers. Now there's one extraction point for auth, and the override is handled explicitly as a separate concern.

### 3. Auth Providers (Policy Config)

Policies declare how to obtain credentials via a tagged union in YAML:

```python
# src/luthien_proxy/credentials/auth_provider.py

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class UserCredentials:
    """Extract from current request headers. Fail if absent."""
    pass


@dataclass(frozen=True)
class ServerKey:
    """Look up operator-provisioned key by name from persistent store."""
    name: str


@dataclass(frozen=True)
class UserThenServer:
    """Try user creds from request, fall back to named server key.

    on_fallback controls behavior when user credential is missing:
    - "fallback": silently use server key
    - "warn": use server key but log a warning + emit metric (default)
    - "fail": reject the request (strictest — treat missing user cred as error)
    """
    name: str
    on_fallback: Literal["fallback", "warn", "fail"] = "warn"


AuthProvider = UserCredentials | ServerKey | UserThenServer
```

YAML representation:

```yaml
# User's own credential (from request headers)
auth_provider: "user_credentials"

# Operator-provisioned server key
auth_provider:
  server_key: "judge-api-key"

# Fallback chain: try user creds, then server key
auth_provider:
  user_then_server: "judge-api-key"
```

Parsing logic:

```python
def parse_auth_provider(raw: str | dict | None) -> AuthProvider:
    if raw is None or raw == "user_credentials":
        return UserCredentials()
    if isinstance(raw, dict):
        if "server_key" in raw:
            return ServerKey(name=raw["server_key"])
        if "user_then_server" in raw:
            return UserThenServer(name=raw["user_then_server"])
    raise ValueError(f"Unknown auth_provider: {raw}")
```

### 4. CredentialManager — Single Service

`CredentialManager` becomes the single service for all credential operations. It absorbs the resolver role and owns the server credential store internally. Policies and the gateway only interact with `CredentialManager` — `CredentialStore` is an internal implementation detail.

```python
# src/luthien_proxy/credential_manager.py (extended)

class CredentialManager:
    """Single service for credential validation, resolution, and storage.

    Existing responsibilities (unchanged):
    - Auth mode enforcement (PROXY_KEY, PASSTHROUGH, BOTH)
    - Credential validation via count_tokens endpoint
    - Hash-based validation caching (Redis or in-process)

    New responsibilities:
    - Auth provider resolution (resolve AuthProvider → Credential per-request)
    - Server credential storage (operator-provisioned keys)
    """

    def __init__(
        self,
        db_pool: DatabasePool | None,
        cache: CredentialCacheProtocol | None,
        encryption_key: bytes | None = None,
    ):
        # ... existing init ...
        self._store = CredentialStore(db_pool, encryption_key) if db_pool else None

    # ---- New: Auth Provider Resolution ----

    async def resolve(
        self,
        provider: AuthProvider,
        context: PolicyContext,
    ) -> Credential:
        """Resolve an auth provider to a credential for this request.

        Raises CredentialError if resolution fails.
        """
        if isinstance(provider, UserCredentials):
            return self._get_user_credential(context)

        if isinstance(provider, ServerKey):
            return await self._get_server_key(provider.name)

        if isinstance(provider, UserThenServer):
            try:
                return self._get_user_credential(context)
            except CredentialError:
                if provider.on_fallback == "fail":
                    raise
                if provider.on_fallback == "warn":
                    logger.warning(
                        "No user credential on request, falling back to server key %r",
                        provider.name,
                    )
                    # TODO: emit credential.fallback_to_server_key metric
                return await self._get_server_key(provider.name)

        raise CredentialError(f"Unknown auth provider type: {type(provider)}")

    def _get_user_credential(self, context: PolicyContext) -> Credential:
        """Read user credential from PolicyContext (set by gateway)."""
        if context.user_credential is None:
            raise CredentialError("No user credential on request context")
        return context.user_credential

    async def _get_server_key(self, name: str) -> Credential:
        if self._store is None:
            raise CredentialError("No credential store configured (no database)")
        cred = await self._store.get(name)
        if cred is None:
            raise CredentialError(f"Server key '{name}' not found")
        return cred

    # ---- New: Server Credential CRUD ----

    async def put_server_credential(self, name: str, credential: Credential) -> None:
        if self._store is None:
            raise CredentialError("No credential store configured")
        await self._store.put(name, credential)

    async def delete_server_credential(self, name: str) -> bool:
        if self._store is None:
            raise CredentialError("No credential store configured")
        return await self._store.delete(name)

    async def list_server_credentials(self) -> list[str]:
        if self._store is None:
            return []
        return await self._store.list_names()


class CredentialError(Exception):
    """Raised when credential resolution fails."""
    pass
```

`CredentialStore` is an internal class (private to `credential_manager.py` or in a `_store.py` submodule). It handles only DB reads/writes + encryption. Policies never import or interact with it.

### 5. Server Credential Store (Internal)

```python
# Internal to CredentialManager — not a public interface

class CredentialStore:
    """DB-backed storage for operator-provisioned credentials."""

    def __init__(self, db_pool: DatabasePool, encryption_key: bytes | None = None):
        self._db = db_pool
        self._fernet = Fernet(encryption_key) if encryption_key else None

    async def get(self, name: str) -> Credential | None: ...
    async def put(self, name: str, credential: Credential) -> None: ...
    async def delete(self, name: str) -> bool: ...
    async def list_names(self) -> list[str]: ...
```

**Schema** (`server_credentials` table):

| Column | Type | Notes |
|--------|------|-------|
| `name` | `TEXT UNIQUE NOT NULL` | Alias for config references (e.g. `"judge-api-key"`) |
| `platform` | `TEXT NOT NULL` | `"anthropic"`, `"openai"` |
| `platform_url` | `TEXT` | Custom base URL, nullable |
| `credential_type` | `TEXT NOT NULL` | `"api_key"` or `"auth_token"` (matches `CredentialType` enum values) |
| `credential_value` | `TEXT NOT NULL` | Plaintext or Fernet-encrypted (see Encryption) |
| `is_encrypted` | `BOOLEAN NOT NULL DEFAULT FALSE` | Whether `credential_value` is Fernet-encrypted |
| `expiry` | `TIMESTAMP` | Nullable, for rotating credentials |
| `owner` | `TEXT` | Nullable, for future multi-tenancy |
| `scope` | `TEXT` | Nullable, for future multi-tenancy |
| `created_at` | `TIMESTAMP NOT NULL` | |
| `updated_at` | `TIMESTAMP NOT NULL` | |

**Encryption**: Opt-in. If `CREDENTIAL_ENCRYPTION_KEY` env var is set (a Fernet key from `cryptography` library), credential values are encrypted at rest. If unset, values are stored as plaintext with a startup warning. Rationale: for SQLite single-user deployments, the encryption key would be co-located on the same disk as the database — encrypting provides no meaningful protection against an attacker who can read the filesystem. For Postgres multi-user deployments, operators should set the encryption key. If the store has encrypted values and the key is absent (or wrong), those credentials are unreadable — the store surfaces clear errors at resolution time rather than failing silently.

**Admin API** for managing server credentials (routed through CredentialManager):

```
POST   /api/admin/credentials          — create/update a server credential
GET    /api/admin/credentials           — list names (no values)
DELETE /api/admin/credentials/{name}    — delete a credential
```

### 6. LLM Client Wrapper

LiteLLM gets wrapped so the rest of the system interacts with `Credential` objects, not raw strings + auth_type flags.

```python
# src/luthien_proxy/llm/judge_client.py

async def judge_completion(
    credential: Credential,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 256,
    api_base: str | None = None,
    response_format: dict | None = None,
) -> str:
    """Make a judge LLM call using the given credential.

    Translates Credential → LiteLLM kwargs internally.
    Returns the response content string.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if api_base:
        kwargs["api_base"] = api_base
    if response_format:
        kwargs["response_format"] = response_format

    # Credential → LiteLLM translation (the part we replace when dropping LiteLLM)
    if credential.credential_type == CredentialType.AUTH_TOKEN:
        kwargs["extra_headers"] = {"Authorization": f"Bearer {credential.value}"}
        # LiteLLM needs a non-None api_key even for bearer auth
        kwargs["api_key"] = "placeholder"
    else:
        kwargs["api_key"] = credential.value

    response = await acompletion(**kwargs)
    response = cast(ModelResponse, response)
    content = cast(Choices, response.choices[0]).message.content
    if content is None:
        raise ValueError("LLM response content is None")
    return content
```

### 7. Integration with PolicyContext

The user's credential (extracted from the incoming request) and the `CredentialManager` are both available on `PolicyContext`:

```python
# Added to PolicyContext
user_credential: Credential | None = None
_credential_manager: CredentialManager | None = None

@property
def credential_manager(self) -> CredentialManager:
    """Access the credential manager. Raises if not configured."""
    if self._credential_manager is None:
        raise CredentialError(
            "No credential manager configured. "
            "Policies using auth_provider require a running gateway with "
            "CredentialManager initialized."
        )
    return self._credential_manager
```

The `credential_manager` property raises a clear `CredentialError` (not `AttributeError` on `None`) when accessed without a configured manager. This mirrors how `_emitter` works — except `_emitter` defaults to `NullEventEmitter()` because all code paths emit events, while `_credential_manager` is only accessed by policies that declare an `auth_provider`. A null object isn't needed — the error is the right behavior.

`user_credential` is set by the gateway in `anthropic_messages()` using the forwarding credential from `resolve_anthropic_client()` (which accounts for `x-anthropic-api-key` overrides).

`_credential_manager` is set at PolicyContext construction and **shared across deepcopy** (not copied), following the same pattern as `_emitter` which holds DB/Redis pool references:

```python
# In PolicyContext.__deepcopy__:
new._credential_manager = self._credential_manager  # shared, not copied
```

This is necessary because `CredentialManager` holds a `DatabasePool` and cache connections that must not be duplicated during parallel policy execution.

**Testing**: `PolicyContext.for_testing()` should accept an optional `credential_manager` parameter. For unit tests that don't need auth provider resolution, omit it — the property will raise only if actually accessed. For tests that exercise auth providers, pass a mock or a real `CredentialManager` with an in-memory store.

### 8. Integration with Existing Policies

Before (ToolCallJudgePolicy):
```python
# In __init__:
self._fallback_api_key = settings.llm_judge_api_key or settings.litellm_master_key or None

# In _evaluate_and_maybe_block_anthropic:
api_key=self._resolve_judge_api_key(context, self._config.api_key, self._fallback_api_key)
```

After:
```python
# In __init__ (from YAML config):
self._auth_provider = parse_auth_provider(config.auth_provider)

# In _evaluate_and_maybe_block_anthropic:
credential = await context.credential_manager.resolve(self._auth_provider, context)
result = await judge_completion(credential, model=self._config.model, messages=prompt, ...)
```

The `_resolve_judge_api_key`, `_extract_passthrough_key`, and the settings-level `llm_judge_api_key`/`litellm_master_key` are all replaced by the auth provider + resolver pattern.

## YAML Config Examples

### Minimal (user credentials, current default behavior)

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    model: "claude-haiku-4-5"
    # auth_provider defaults to "user_credentials" if omitted
```

### Server-managed key for judge calls

```yaml
policy:
  class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
  config:
    model: "claude-haiku-4-5"
    auth_provider:
      server_key: "judge-anthropic"
```

### Fallback chain (typical production setup)

```yaml
policy:
  class: "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"
  config:
    model: "claude-haiku-4-5"
    instructions: "Block PII in responses"
    auth_provider:
      user_then_server: "judge-fallback"
```

### Multi-policy with different auth per sub-policy

```yaml
policy:
  class: "luthien_proxy.policies.multi_policy:MultiPolicy"
  config:
    policies:
      - class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
        config:
          model: "claude-haiku-4-5"
          auth_provider:
            server_key: "security-judge"  # operator pays for security checks
      - class: "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"
        config:
          model: "claude-haiku-4-5"
          instructions: "Ensure code comments are in English"
          auth_provider: "user_credentials"  # user pays for style checks
```

## Migration Plan

Each step is a separate PR. Steps 1-2 are already in progress.

### Step 1: OAuth bug fix (PR #488)
Fix the transport-header handling for OAuth tokens in judge calls. Standalone bug fix.

### Step 2: Remove prefix-based heuristics (PR #487)
Remove `is_anthropic_api_key()` and the `oauth_via_api_key` code path. Transport header is sole discriminator.

### Step 3: Credential model + single extraction point
- Add `Credential` dataclass and `CredentialType` enum to `src/luthien_proxy/credentials/`
- Add `AuthProvider` types and `parse_auth_provider()`
- Refactor gateway to use `get_request_credential()` shared dependency
- Add `user_credential` and `_credential_manager` to `PolicyContext` (with deepcopy sharing)
- No behavioral changes yet — existing paths still work alongside new types

### Step 4: Server credential store + CredentialManager extensions
- Add `CredentialStore` (internal to `credential_manager.py` or private submodule)
- Add `server_credentials` migration (Postgres + SQLite)
- Add `resolve()`, `put_server_credential()`, `delete_server_credential()`, `list_server_credentials()` to `CredentialManager`
- Add admin API endpoints for credential CRUD
- Add `CREDENTIAL_ENCRYPTION_KEY` env var handling

### Step 5a: Migrate judge policies to auth providers
- Add `auth_provider` config field to `ToolCallJudgePolicy` and `SimpleLLMPolicy`
- Replace `_resolve_judge_api_key` / `_extract_passthrough_key` with `context.credential_manager.resolve()` calls
- Add `judge_completion()` wrapper, replace direct `litellm.acompletion(api_key=...)` calls
- Update tests for both policies and both utils modules

### Step 5b: Deprecate legacy credential settings
- Deprecate `llm_judge_api_key`, `litellm_master_key` settings (keep as fallback for one release)
- Add migration docs explaining how to move from env vars to auth provider config
- Remove `_resolve_judge_api_key` and `_extract_passthrough_key` from `BasePolicy`

### Step 5c: Credential documentation + example-driven tests
- Add developer docs (in `docs/` or `dev/`) covering credential configuration with worked examples:
  - Single policy with user credentials (default, simplest case)
  - Single policy with a server-provisioned API key
  - Fallback chain: user creds with server key fallback
  - Multi-policy with different auth providers per sub-policy (operator pays for security, user pays for style)
  - `x-anthropic-api-key` override: auth with proxy key, forward with a different key
  - Error cases: missing user cred with `on_fallback: "fail"`, missing server key, no credential manager
- Write integration tests that load each YAML example, exercise the credential resolution path, and assert the right credential reaches the LLM client. These tests validate the docs — if an example is wrong, a test fails.
- Use `sqlite_e2e` or `mock_e2e` test infra where appropriate for end-to-end validation of the examples.

### Step 6: Cleanup
- Remove deprecated settings after migration period

## Security Considerations

**What's encrypted at rest**: `server_credentials.credential_value`, if `CREDENTIAL_ENCRYPTION_KEY` is set. Encryption is opt-in — see Server Credential Store section for rationale.

**What's NOT stored**: User credentials. They exist only in memory during request processing. The existing validation cache stores SHA-256 hashes + validity flags, never raw credential values. This doesn't change.

**Key management**: `CREDENTIAL_ENCRYPTION_KEY` should be generated with `Fernet.generate_key()` and treated like any other secret. For Postgres multi-user deployments, this should always be set. For SQLite single-user deployments, it's optional — the encryption key and DB file are co-located on disk, so encryption provides no real protection against filesystem access.

**Admin API access**: Server credential CRUD requires `ADMIN_API_KEY` authentication (same as existing policy management endpoints).

**Graceful degradation**: If `CREDENTIAL_ENCRYPTION_KEY` is unset, the store works with plaintext values (logs a startup warning). If the key is set but wrong (can't decrypt existing values), those credentials are unreadable — resolution fails with a clear error. Policies using `user_credentials` always work regardless of store state.

## Design Decisions Log

Decisions made during design review (2026-03-31 – 2026-04-01), with rationale from 3 rounds of adversarial review:

1. **`CredentialType` is an enum matching existing vocabulary** — Uses `"api_key"` / `"auth_token"` to match `anthropic_client_cache.py`'s existing `Literal["api_key", "auth_token"]`. One vocabulary everywhere: code, DB schema, config. Avoids introducing `"oauth_token"` which would require translation at every boundary.

2. **Single extraction via linear dependency chain** — `get_request_credential()` extracts the credential once from headers. `verify_token()` depends on it for validation, `resolve_anthropic_client()` depends on `verify_token`. Each runs once because of the linear chain. Eliminates the current double-extraction where both `verify_token` and `resolve_anthropic_client` independently parse headers.

3. **`CredentialManager` is the single service** — Absorbs the resolver role and owns the credential store internally. Policies interact only with `CredentialManager`. `CredentialStore` is an internal implementation detail (DB wrapper + encryption), never imported by consumers. This avoids the "too many thin abstractions" problem of having `CredentialResolver` + `CredentialStore` + `CredentialManager` as three public classes.

4. **Resolver reads from `context.user_credential`, not raw headers** — The gateway sets it once on `PolicyContext`. The resolver reads from there. No duplicate extraction, no re-parsing headers in policy code.

5. **`CredentialManager` on PolicyContext, shared not copied** — Follows the `_emitter` pattern in `__deepcopy__`: the reference is shared across copies during parallel policy execution. This is necessary because `CredentialManager` holds DB pool and cache connections. Committed decision (not "injected into PolicyContext **or** available as a dependency").

6. **`UserThenServer` fallback is observable** — Default `on_fallback="warn"` logs + emits a metric when falling back to server key. Silent fallback on misconfigured gateway / stripped headers was identified as a risk. Operators can also set `"fail"` to reject requests without user creds.

7. **Encryption is opt-in** — For SQLite single-user deployments, the encryption key is co-located with the DB on disk. Mandatory encryption would be security theater. For Postgres multi-user, operators set `CREDENTIAL_ENCRYPTION_KEY`.

8. **Migration is 3+ PRs, not one** — Step 5 split into: (5a) judge policy migration, (5b) settings deprecation. Each touches different concerns and can be reviewed independently.

9. **Gateway forwarding path uses Credential via linear dependency chain** — Unlike the earlier "leave forwarding alone" approach, `resolve_anthropic_client()` now receives a `Credential` from `verify_token()` (which gets it from `get_request_credential()`). Uses `credential.credential_type.value` as the `auth_type` for the client cache.

10. **`x-anthropic-api-key` is the forwarding credential, not the auth credential** — A client may authenticate with one key (proxy key, OAuth token) and override the backend forwarding key via `x-anthropic-api-key`. `PolicyContext.user_credential` is set to the forwarding credential (override if present, else auth credential), because policies care about the identity making the backend request. `resolve_anthropic_client` returns both the client and the forwarding credential for this purpose.

11. **`credential_manager` property raises, no null object** — `PolicyContext.credential_manager` is a property that raises `CredentialError` if `_credential_manager` is `None`. Unlike `_emitter` (which defaults to `NullEventEmitter` because all paths emit), `_credential_manager` is only accessed by policies with `auth_provider` config. The error is the right behavior — it's a configuration mistake, not a normal path. `for_testing()` accepts optional `credential_manager` for tests that exercise auth providers.

12. **Default auth provider is `user_credentials`** — When `auth_provider` is omitted from policy config, defaults to `UserCredentials()`. This matches current behavior and keeps server keys opt-in. No surprise changes for existing deployments.

13. **Credential expiry: warn, don't block** — `CredentialStore.get()` logs a warning when a credential is within 7 days of expiry or already expired. Resolution still succeeds — the backend will reject expired credentials on its own. Not in initial implementation; fast follow-up PR.

14. **LiteLLM OAuth: wrap minimally** — `judge_completion()` handles AUTH_TOKEN → `extra_headers` already. Fix LiteLLM quirks case-by-case in the wrapper. Don't invest heavily since LiteLLM is being deprecated.

15. **Audit logging: mutations only** — Log server credential put/delete via the event system. Log credential *name*, never the value. No logging for reads (too noisy) or user credential extraction (every request).
