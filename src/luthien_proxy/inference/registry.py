"""DB-backed registry of named `InferenceProvider` instances.

Operators define providers in the `inference_providers` table (via admin
API or the `/inference-providers` UI). Callers look them up by name.

Design notes:

- Single-row-per-name DB table (see `migrations/**/014_add_inference_providers.sql`).
- We cache the `ProviderRecord` (DB row), not the constructed provider
  instance, and rebuild the provider on every `get()`. Construction is
  pure-Python cheap for both current backends (no httpx client spin-up,
  no subprocess), so the cost is negligible and the correctness win is
  real: credential rotation propagates immediately. If a future backend
  holds long-lived async state, switch to an instance cache then — not
  speculatively now.
- Cold-cache concurrency is guarded by a single `asyncio.Lock`. Under
  `asyncio.gather(get("p"), get("p"))` from an empty cache, the DB read
  runs exactly once. The lock is held only across the record fetch, not
  across credential resolution or provider construction.
- Dispatch on `backend_type` via a constructor map. Unknown types raise
  a typed error rather than KeyError'ing deep in provider code.
- Credential resolution is a *soft* reference: we look up
  `credential_name` via `CredentialManager.resolve_server_credential`
  at every `get()`, so cred deletion surfaces as a clear error only
  when someone tries to use the dangling reference.
- Backends that can tolerate a missing configured credential (currently
  `direct_api`, which supports per-call `credential_override`) are built
  in a "null-credential" mode that raises on `complete()` entry if no
  override is supplied. Backends that can't (currently `claude_code`)
  raise `MissingCredentialError` at construction time.

PR #4 wires judge policies onto the registry; PR #5 adds a `/ping`
endpoint for the policy-testing UI. Neither is implemented here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from luthien_proxy.credential_manager import CredentialManager
from luthien_proxy.credentials.credential import Credential, CredentialError, CredentialType
from luthien_proxy.inference.base import InferenceError, InferenceProvider, InferenceProviderError, InferenceResult
from luthien_proxy.inference.claude_code import ClaudeCodeProvider
from luthien_proxy.inference.direct_api import DirectApiProvider
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)

#: How long a `ProviderRecord` stays in the in-memory cache.
#:
#: This caches only the DB row (not a constructed provider + credential),
#: so the TTL bounds *config* staleness after an `inference_providers`
#: edit. Credentials are always resolved fresh on `get()`, so this TTL
#: does not delay credential rotations.
DEFAULT_CACHE_TTL_SECONDS = 60.0

#: Hard cap on the serialized size of the `config` JSON column.
#:
#: Same budget as PR #605's policy-config ceiling: keeps UI rendering
#: responsive and prevents a hostile/misconfigured operator from
#: wedging the admin page with a megabyte of dict.
MAX_CONFIG_JSON_BYTES = 64 * 1024


class InferenceRegistryError(InferenceProviderError):
    """Base class for registry-level errors.

    Subclass of `InferenceProviderError` so callers that already catch
    inference failures don't need a separate branch for "registry said
    no".
    """


class UnknownBackendTypeError(InferenceRegistryError):
    """The stored `backend_type` isn't registered in the constructor map.

    Typical cause: operator-written row references a backend that's been
    removed or hasn't been deployed yet. The error message includes both
    the provider name and the offending backend_type.
    """


class ProviderNotFoundError(InferenceRegistryError):
    """No `inference_providers` row with the given name."""


class MissingCredentialError(InferenceRegistryError):
    """The provider references a `credential_name` that doesn't exist.

    Soft-reference behavior: the registry detects this at `get()` time
    rather than at write time. An operator who deletes a credential
    doesn't cascade-break all providers — the breakage surfaces only
    when something actually tries to use the dangling reference.
    """


class NullCredentialError(InferenceError):
    """Null-credential provider was called without `credential_override`.

    A provider constructed with `credential_name IS NULL` got a
    `complete()` call that didn't supply a per-request override. Only
    `direct_api` providers can reach this state (via
    `NullCredentialDirectApiProvider`); `claude_code` refuses at
    construction time because the CLI has no override path.
    """


@dataclass(frozen=True)
class ProviderRecord:
    """DB row for an `inference_providers` entry."""

    name: str
    backend_type: str
    credential_name: str | None
    default_model: str
    config: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None


#: Callable that builds an `InferenceProvider` from a record + resolved cred.
#:
#: The credential is `None` if the record has `credential_name IS NULL`.
#: Each backend decides whether it tolerates that (DirectApi does, via
#: the null-credential wrapper below; ClaudeCode does not).
ProviderFactory = Callable[
    [ProviderRecord, Any],  # (record, Credential | None)
    InferenceProvider,
]


class NullCredentialDirectApiProvider(DirectApiProvider):
    """A `DirectApiProvider` that refuses `complete()` without an override.

    Built when a `direct_api` row has `credential_name IS NULL`. The
    provider is a valid registry entry — per-call `credential_override`
    is the documented path — but sending `authorization: Bearer ` to
    Anthropic would produce a 401 with no useful diagnostic. This
    wrapper fails early with a clear `NullCredentialError` instead.
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format: dict[str, Any] | None = None,
        credential_override: Credential | None = None,
    ) -> InferenceResult:
        """Require a `credential_override`; otherwise raise."""
        if credential_override is None:
            raise NullCredentialError(
                f"DirectApiProvider {self.name!r} was configured without a credential. "
                "This provider only serves requests that supply `credential_override` "
                "(e.g. user-credential passthrough). Configure a `credential_name` on "
                "the provider row to accept credential-less calls."
            )
        return await super().complete(
            messages,
            model=model,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            credential_override=credential_override,
        )


def _build_claude_code(record: ProviderRecord, credential: Any) -> InferenceProvider:
    """Construct a ClaudeCodeProvider from a DB record.

    `ClaudeCodeProvider` requires a credential (the OAuth access token
    it injects into the subprocess env). Raise if one wasn't configured.
    """
    if credential is None:
        raise MissingCredentialError(
            f"Provider {record.name!r} (backend=claude_code) requires credential_name "
            "to be set — the claude CLI cannot authenticate without one."
        )
    timeout = float(record.config.get("timeout_seconds", 120.0))
    return ClaudeCodeProvider(
        name=record.name,
        credential=credential,
        default_model=record.default_model,
        timeout_seconds=timeout,
    )


def _build_direct_api(record: ProviderRecord, credential: Any) -> InferenceProvider:
    """Construct a DirectApiProvider from a DB record.

    Unlike claude_code, a direct_api provider without a configured
    credential is a legitimate mode (per-request `credential_override`),
    but we build the null-credential wrapper so a forgotten override
    fails loudly at entry instead of shipping an empty Bearer header.
    """
    api_base = record.config.get("api_base")
    resolved_api_base = api_base if isinstance(api_base, str) else None
    if credential is None:
        # Placeholder credential satisfies the base constructor; the
        # wrapper guarantees it's never actually used.
        placeholder = Credential(
            value="",
            credential_type=CredentialType.API_KEY,
            platform="anthropic",
        )
        return NullCredentialDirectApiProvider(
            name=record.name,
            credential=placeholder,
            default_model=record.default_model,
            api_base=resolved_api_base,
        )
    return DirectApiProvider(
        name=record.name,
        credential=credential,
        default_model=record.default_model,
        api_base=resolved_api_base,
    )


#: Registry of supported `backend_type` values. Extend by adding a factory
#: here rather than branching inside `InferenceProviderRegistry.get()`.
DEFAULT_BACKEND_FACTORIES: dict[str, ProviderFactory] = {
    "claude_code": _build_claude_code,
    "direct_api": _build_direct_api,
}


class InferenceProviderRegistry:
    """Loads and exposes named `InferenceProvider` instances.

    Lifecycle: constructed at startup with a DB pool and the already-
    initialized `CredentialManager`. `await initialize()` is currently a
    no-op (reserved for future eager-warm behavior). `await close()` is
    also a no-op — we no longer cache provider instances, so there's no
    persistent async state for the registry to release. We keep the
    method so the lifespan wiring in `main.py` can stay symmetric with
    `CredentialManager.close()`.
    """

    def __init__(
        self,
        db_pool: DatabasePool | None,
        credential_manager: CredentialManager,
        *,
        factories: dict[str, ProviderFactory] | None = None,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        """Wire the registry to its DB pool and credential resolver.

        Args:
            db_pool: Database pool backing `inference_providers`. `None`
                disables persistence (the registry still validates names
                but every lookup returns `ProviderNotFoundError`).
            credential_manager: The already-initialized manager used to
                resolve `credential_name` -> `Credential` on each `get()`.
            factories: Override the `backend_type` -> constructor map.
                Useful in tests.
            cache_ttl_seconds: How long to keep a `ProviderRecord` in
                the in-memory cache.
        """
        self._db_pool = db_pool
        self._credential_manager = credential_manager
        self._factories = dict(factories or DEFAULT_BACKEND_FACTORIES)
        self._cache_ttl = cache_ttl_seconds
        # Cache of DB rows only. Credentials resolve fresh per get() so
        # cred rotations propagate without a TTL-length stale window.
        self._record_cache: dict[str, tuple[float, ProviderRecord]] = {}
        # Single lock is enough for v1: guards concurrent cold-cache DB
        # reads against the same name. Per-name locks would be a throughput
        # win only once we see many concurrent cache-misses on *different*
        # names, which the judge workload doesn't do.
        self._cache_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Reserved for future eager-load behavior. Currently a no-op."""
        return None

    async def list(self) -> list[ProviderRecord]:
        """Return all configured provider records, ordered by name."""
        if self._db_pool is None:
            return []
        pool = await self._db_pool.get_pool()
        rows = await pool.fetch(
            "SELECT name, backend_type, credential_name, default_model, config, "
            "created_at, updated_at FROM inference_providers ORDER BY name"
        )
        return [_row_to_record(dict(row)) for row in rows]

    async def get_record(self, name: str) -> ProviderRecord | None:
        """Fetch a single record by name, or `None` if absent.

        Bypasses the record cache — used by the admin API where we want
        the authoritative row. `get()` uses `_resolve_record` instead,
        which shares the cache and the lock.
        """
        if self._db_pool is None:
            return None
        pool = await self._db_pool.get_pool()
        row = await pool.fetchrow(
            "SELECT name, backend_type, credential_name, default_model, config, "
            "created_at, updated_at FROM inference_providers WHERE name = $1",
            name,
        )
        if row is None:
            return None
        return _row_to_record(dict(row))

    async def get(self, name: str) -> InferenceProvider:
        """Resolve `name` to a fresh `InferenceProvider` instance.

        Cache semantics:
        - The DB row is cached for `cache_ttl_seconds` (bounded config
          staleness after an admin-API edit).
        - The resolved `Credential` is NOT cached at the registry layer
          (the `CredentialManager`'s 60s cache still applies, but it
          invalidates on writes, so rotation takes effect immediately).
        - The provider instance is NOT cached; a fresh one is built per
          call. Construction is pure-Python cheap for both current
          backends.
        """
        record = await self._resolve_record(name)

        factory = self._factories.get(record.backend_type)
        if factory is None:
            raise UnknownBackendTypeError(
                f"Provider {name!r} has backend_type={record.backend_type!r} "
                f"which is not registered. Known: {sorted(self._factories)!r}"
            )

        credential: Credential | None = None
        if record.credential_name is not None:
            try:
                credential = await self._credential_manager.resolve_server_credential(record.credential_name)
            except CredentialError as exc:
                raise MissingCredentialError(
                    f"Provider {name!r} references credential_name={record.credential_name!r}, "
                    f"but it could not be resolved: {exc}"
                ) from exc

        return factory(record, credential)

    async def _resolve_record(self, name: str) -> ProviderRecord:
        """Return a `ProviderRecord`, serving from cache when fresh.

        The lock serializes only cold-cache DB reads; callers that land
        inside the TTL see the cached record without waiting. The
        two-phase check (outside lock, then inside lock) avoids stampeding
        the DB under concurrent cold-cache `get()` on the same name.
        """
        cached = self._record_cache.get(name)
        now = time.time()
        if cached is not None and now - cached[0] < self._cache_ttl:
            return cached[1]

        async with self._cache_lock:
            cached = self._record_cache.get(name)
            now = time.time()
            if cached is not None and now - cached[0] < self._cache_ttl:
                return cached[1]

            record = await self.get_record(name)
            if record is None:
                # Drop any stale entry so a deleted row doesn't linger.
                self._record_cache.pop(name, None)
                raise ProviderNotFoundError(f"Inference provider {name!r} not found")
            self._record_cache[name] = (now, record)
            return record

    async def put(self, record: ProviderRecord) -> None:
        """Insert or update a provider row; drops the cached record."""
        if self._db_pool is None:
            raise InferenceRegistryError("No DB pool configured; cannot persist provider")
        _validate_record(record, self._factories)

        pool = await self._db_pool.get_pool()
        config_json = json.dumps(record.config, ensure_ascii=False)
        # We passed Pydantic's size gate; re-check here so direct put()
        # callers (tests, PR #4's policy wiring) can't accidentally
        # bypass the UI ceiling.
        if len(config_json.encode("utf-8")) > MAX_CONFIG_JSON_BYTES:
            raise InferenceRegistryError(
                f"Provider {record.name!r}: config JSON exceeds {MAX_CONFIG_JSON_BYTES} bytes."
            )
        # NOTE: `created_at` / `updated_at` are written as NOW() literals
        # in the DDL default. We pass them explicitly here to keep the
        # SQL readable AND to avoid PR #600's positional-arg-reuse bug
        # (`$N, $N` → `?, ?`). Two literal `NOW()` calls are cheap.
        await pool.execute(
            """
            INSERT INTO inference_providers
                (name, backend_type, credential_name, default_model, config, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
            ON CONFLICT (name) DO UPDATE SET
                backend_type = EXCLUDED.backend_type,
                credential_name = EXCLUDED.credential_name,
                default_model = EXCLUDED.default_model,
                config = EXCLUDED.config,
                updated_at = NOW()
            """,
            record.name,
            record.backend_type,
            record.credential_name,
            record.default_model,
            config_json,
        )
        self._invalidate(record.name)
        logger.info(
            "Inference provider %r stored (backend=%s, credential=%s)",
            record.name,
            record.backend_type,
            record.credential_name,
        )

    async def delete(self, name: str) -> bool:
        """Delete a provider row. Returns True if a row existed."""
        if self._db_pool is None:
            return False
        pool = await self._db_pool.get_pool()
        result = await pool.execute(
            "DELETE FROM inference_providers WHERE name = $1",
            name,
        )
        count_str = str(result).rsplit(" ", 1)[-1]
        deleted = count_str != "0"
        self._invalidate(name)
        if deleted:
            logger.info("Inference provider %r deleted", name)
        return deleted

    async def close(self) -> None:
        """Clear the record cache.

        No provider instances are held, so there's nothing async to
        drain. Kept for lifespan symmetry with `CredentialManager.close()`.
        """
        self._record_cache.clear()

    def _invalidate(self, name: str) -> None:
        """Drop a cached record on update / delete."""
        self._record_cache.pop(name, None)

    def known_backend_types(self) -> tuple[str, ...]:
        """Return the backend_type keys this registry can construct.

        Surfaced on admin-API list responses so the UI can mark rows
        with an unregistered backend_type (operator-written row whose
        backend was removed or hasn't been deployed yet) rather than
        silently rewriting them on edit.
        """
        return tuple(sorted(self._factories))


def _row_to_record(row: dict[str, Any]) -> ProviderRecord:
    """Convert a DB row dict to a `ProviderRecord`.

    `config` is stored as JSONB in postgres and as TEXT in sqlite;
    normalize both to a Python dict.

    TODO(post-merge): normalize `created_at` / `updated_at` to an
    ISO8601 string with a trailing `Z`. Postgres returns a timezone-
    aware datetime stringified as `2026-01-01 00:00:00+00:00`; SQLite
    returns a naive `2026-01-01 00:00:00`. Callers currently see raw
    str(datetime) in both shapes; the UI doesn't parse either today,
    so the drift is cosmetic — fix in a small follow-up PR.
    """
    raw_config = row["config"]
    if isinstance(raw_config, str):
        config = json.loads(raw_config) if raw_config else {}
    elif isinstance(raw_config, dict):
        config = raw_config
    else:
        config = {}

    credential_name = row["credential_name"]
    created_at = row.get("created_at")
    updated_at = row.get("updated_at")
    return ProviderRecord(
        name=str(row["name"]),
        backend_type=str(row["backend_type"]),
        credential_name=str(credential_name) if credential_name is not None else None,
        default_model=str(row["default_model"]),
        config=config,
        created_at=str(created_at) if created_at is not None else None,
        updated_at=str(updated_at) if updated_at is not None else None,
    )


def _validate_record(record: ProviderRecord, factories: dict[str, ProviderFactory]) -> None:
    """Cheap pre-write checks so invalid rows don't land in the DB.

    TODO(post-merge): add a DB-level CHECK on `credential_name` regex.
    Today Pydantic validates incoming API bodies and this function
    validates programmatic writes, but a direct SQL INSERT could still
    seed an invalid name. Same goes for `list()` pagination — the UI
    currently renders every row; fine for single-digit counts, add an
    offset/limit once registries routinely hold >50 providers.
    """
    if record.backend_type not in factories:
        raise UnknownBackendTypeError(
            f"backend_type={record.backend_type!r} is not registered. Known: {sorted(factories)!r}"
        )
    if not record.default_model:
        raise InferenceRegistryError(f"Provider {record.name!r}: default_model must be non-empty")


__all__ = [
    "DEFAULT_BACKEND_FACTORIES",
    "MAX_CONFIG_JSON_BYTES",
    "InferenceProviderRegistry",
    "InferenceRegistryError",
    "MissingCredentialError",
    "NullCredentialDirectApiProvider",
    "NullCredentialError",
    "ProviderFactory",
    "ProviderNotFoundError",
    "ProviderRecord",
    "UnknownBackendTypeError",
]
