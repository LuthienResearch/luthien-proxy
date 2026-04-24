"""DB-backed registry of named `InferenceProvider` instances.

Operators define providers in the `inference_providers` table (via admin
API or the `/inference-providers` UI). Callers look them up by name.

Design mirrors `CredentialManager`'s server-credential store:
- Single-row-per-name DB table (see `migrations/**/014_add_inference_providers.sql`).
- In-memory TTL cache so hot paths don't hit the DB per call.
- Dispatch on `backend_type` via a constructor map. Unknown types raise
  a typed error rather than KeyError'ing deep in provider code.
- Credential resolution is a *soft* reference: the registry looks up
  `credential_name` in `CredentialManager` at `get()` time, so cred
  deletion doesn't break providers until someone tries to use them. The
  error at use-time clearly names the missing credential.
- Provider instances are cached with a 60s TTL. When a provider is
  updated or deleted, the cache entry is dropped and any held provider
  is `close()`d so HTTP clients / subprocess resources don't leak.

PR #4 wires judge policies onto the registry; PR #5 adds a `/ping`
endpoint for the policy-testing UI. Neither is implemented here.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from luthien_proxy.credential_manager import CredentialManager
from luthien_proxy.credentials.credential import Credential, CredentialError, CredentialType
from luthien_proxy.inference.base import InferenceProvider, InferenceProviderError
from luthien_proxy.inference.claude_code import ClaudeCodeProvider
from luthien_proxy.inference.direct_api import DirectApiProvider
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)

#: How long a constructed provider instance stays in the in-memory cache.
#: Matches `CredentialManager._server_key_ttl` so a restart-free cred
#: rotation propagates in ~1 TTL regardless of which layer you poke.
DEFAULT_CACHE_TTL_SECONDS = 60.0


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
#: Each backend decides whether it tolerates that (DirectApi does —
#: credentials flow via `credential_override`; ClaudeCode does not).
ProviderFactory = Callable[
    [ProviderRecord, Any],  # (record, Credential | None)
    InferenceProvider,
]


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
    credential is valid — callers pass `credential_override` per-request.
    We use a sentinel-less dummy to satisfy the constructor's required
    arg when no credential is configured; the dummy is never used because
    every call path goes through `credential_override`.
    """
    if credential is None:
        # DirectApi supports per-request override. We still need a value
        # for the stored credential; construct a placeholder that the
        # provider will log-and-fail if ever used unadorned.
        credential = Credential(
            value="",
            credential_type=CredentialType.API_KEY,
            platform="anthropic",
        )
    api_base = record.config.get("api_base")
    return DirectApiProvider(
        name=record.name,
        credential=credential,
        default_model=record.default_model,
        api_base=api_base if isinstance(api_base, str) else None,
    )


#: Registry of supported `backend_type` values. Extend by adding a factory
#: here rather than branching inside `InferenceProviderRegistry.get()`.
DEFAULT_BACKEND_FACTORIES: dict[str, ProviderFactory] = {
    "claude_code": _build_claude_code,
    "direct_api": _build_direct_api,
}


class InferenceProviderRegistry:
    """Loads, caches, and exposes named `InferenceProvider` instances.

    Lifecycle: constructed at startup with a DB pool and the already-
    initialized `CredentialManager`. `await initialize()` is currently a
    no-op (reserved for future eager-warm behavior). `await close()`
    drains cached providers that hold async resources.
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
                resolve `credential_name` -> `Credential` on `get()`.
            factories: Override the `backend_type` -> constructor map.
                Useful in tests.
            cache_ttl_seconds: How long to keep a constructed provider in
                the in-memory cache.
        """
        self._db_pool = db_pool
        self._credential_manager = credential_manager
        self._factories = dict(factories or DEFAULT_BACKEND_FACTORIES)
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, InferenceProvider]] = {}

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
        """Fetch a single record by name, or `None` if absent."""
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
        """Resolve `name` to a live `InferenceProvider`.

        Cached instances are served if fresh; on miss we load the record,
        resolve its credential, and build a provider via the factory map.
        """
        now = time.time()
        cached = self._cache.get(name)
        if cached is not None:
            cached_at, provider = cached
            if now - cached_at < self._cache_ttl:
                return provider
            # Expired — drop and rebuild. Best-effort close on the stale
            # instance so HTTP clients / subprocesses aren't orphaned.
            self._cache.pop(name, None)
            await _safe_close(provider)

        record = await self.get_record(name)
        if record is None:
            raise ProviderNotFoundError(f"Inference provider {name!r} not found")

        factory = self._factories.get(record.backend_type)
        if factory is None:
            raise UnknownBackendTypeError(
                f"Provider {name!r} has backend_type={record.backend_type!r} "
                f"which is not registered. Known: {sorted(self._factories)!r}"
            )

        credential = None
        if record.credential_name is not None:
            try:
                credential = await self._credential_manager._get_server_key(record.credential_name)
            except CredentialError as exc:
                raise MissingCredentialError(
                    f"Provider {name!r} references credential_name={record.credential_name!r}, "
                    f"but it could not be resolved: {exc}"
                ) from exc

        provider = factory(record, credential)
        self._cache[name] = (now, provider)
        return provider

    async def put(self, record: ProviderRecord) -> None:
        """Insert or update a provider row; drops any cached instance.

        We pass `created_at`/`updated_at` as distinct positional args to
        avoid the SQLite translator's positional-arg-reuse bug (fixed in
        PR #600, not yet merged into this branch's base).
        """
        if self._db_pool is None:
            raise InferenceRegistryError("No DB pool configured; cannot persist provider")
        _validate_record(record, self._factories)

        pool = await self._db_pool.get_pool()
        config_json = json.dumps(record.config, ensure_ascii=False)
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
        await self._invalidate(record.name)
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
        await self._invalidate(name)
        if deleted:
            logger.info("Inference provider %r deleted", name)
        return deleted

    async def close(self) -> None:
        """Drain cached providers and release any held resources."""
        cached = list(self._cache.values())
        self._cache.clear()
        for _, provider in cached:
            await _safe_close(provider)

    async def _invalidate(self, name: str) -> None:
        """Drop a cached provider (on update or delete), closing it first."""
        cached = self._cache.pop(name, None)
        if cached is not None:
            _, provider = cached
            await _safe_close(provider)


async def _safe_close(provider: InferenceProvider) -> None:
    """Call `provider.close()` and swallow any error to a warning.

    We don't want a bad `close()` on one provider to block cleanup of
    the rest of the cache during shutdown.
    """
    close: Callable[[], Awaitable[None]] = provider.close
    try:
        await close()
    except Exception as exc:  # noqa: BLE001 - best-effort cleanup
        logger.warning("Error closing inference provider %r: %r", provider.name, exc)


def _row_to_record(row: dict[str, Any]) -> ProviderRecord:
    """Convert a DB row dict to a `ProviderRecord`.

    `config` is stored as JSONB in postgres and as TEXT in sqlite;
    normalize both to a Python dict.
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
    """Cheap pre-write checks so invalid rows don't land in the DB."""
    if record.backend_type not in factories:
        raise UnknownBackendTypeError(
            f"backend_type={record.backend_type!r} is not registered. Known: {sorted(factories)!r}"
        )
    if not record.default_model:
        raise InferenceRegistryError(f"Provider {record.name!r}: default_model must be non-empty")


__all__ = [
    "DEFAULT_BACKEND_FACTORIES",
    "InferenceProviderRegistry",
    "InferenceRegistryError",
    "MissingCredentialError",
    "ProviderFactory",
    "ProviderNotFoundError",
    "ProviderRecord",
    "UnknownBackendTypeError",
]
