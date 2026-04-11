"""Credential validation and caching for passthrough authentication.

Manages configurable auth modes (client_key, passthrough, both) and validates
Anthropic credentials via the free count_tokens endpoint, caching results.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx

from luthien_proxy.credentials.auth_provider import (
    AuthProvider,
    ServerKey,
    UserCredentials,
    UserThenServer,
)
from luthien_proxy.credentials.credential import Credential, CredentialError
from luthien_proxy.credentials.store import CredentialStore
from luthien_proxy.utils.credential_cache import CredentialCacheProtocol
from luthien_proxy.utils.db import DatabasePool

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "luthien:auth:cred:"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages/count_tokens"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_BETA = "token-counting-2024-11-01"

# Minimal payload for credential validation (free endpoint).
# OAuth tokens and API keys both have access to haiku.
VALIDATION_MODEL = "claude-haiku-4-5-20251001"
VALIDATION_PAYLOAD = {
    "model": VALIDATION_MODEL,
    "messages": [{"role": "user", "content": "hi"}],
}


class AuthMode(str, Enum):
    """Authentication mode for the gateway."""

    CLIENT_KEY = "client_key"
    PASSTHROUGH = "passthrough"
    BOTH = "both"


# Legacy auth_mode values that pre-date PR #524's rename. Tolerated on read so
# that a Postgres gateway restarted before migration 013 has applied (or an
# operator who missed renaming AUTH_MODE in their environment) doesn't
# crash-loop. Symmetrically applied to DB rows and the AUTH_MODE env var.
# TODO(post-v0.2): remove this dict and parse_auth_mode's fallback branch
# once all deployments have migrated past PR #535. The regression tests
# (test_tolerates_legacy_proxy_key_row, test_legacy_proxy_key_alias,
# test_auth_mode_env_var_legacy_proxy_key_is_tolerated) should be deleted
# at the same time.
LEGACY_AUTH_MODE_ALIASES: dict[str, AuthMode] = {
    "proxy_key": AuthMode.CLIENT_KEY,
}


def parse_auth_mode(raw: str, source: str = "auth_config.auth_mode") -> AuthMode:
    """Parse an auth_mode value, tolerating pre-#524 aliases with a warning.

    Args:
        raw: The raw string to parse.
        source: Human-readable description of where `raw` came from; included
            in the deprecation warning so operators know what to fix.

    Raises:
        ValueError: if `raw` is neither a current `AuthMode` value nor a
            legacy alias.
    """
    try:
        return AuthMode(raw)
    except ValueError:
        alias = LEGACY_AUTH_MODE_ALIASES.get(raw)
        if alias is not None:
            logger.warning(
                "%s=%r is a pre-PR-#524 value; treating as %r. "
                "Rename to '%s' — the legacy alias will be removed in a follow-up release.",
                source,
                raw,
                alias.value,
                alias.value,
            )
            return alias
        raise


@dataclass(frozen=True)
class AuthConfig:
    """Current auth configuration (loaded from DB)."""

    auth_mode: AuthMode
    validate_credentials: bool
    valid_cache_ttl_seconds: int
    invalid_cache_ttl_seconds: int
    updated_at: str | None = None
    updated_by: str | None = None


@dataclass(frozen=True)
class CachedCredential:
    """A cached credential validation result."""

    key_hash: str
    valid: bool
    validated_at: float
    last_used_at: float


def hash_credential(api_key: str) -> str:
    """SHA-256 hash of a credential for cache lookup and display."""
    return hashlib.sha256(api_key.encode()).hexdigest()


class CredentialManager:
    """Manages auth configuration and credential validation caching.

    Auth config is stored in the `auth_config` DB table (single-row, id=1).
    Credential validation results are cached with configurable TTLs.
    """

    def __init__(
        self,
        db_pool: DatabasePool | None,
        cache: CredentialCacheProtocol | None,
        encryption_key: bytes | None = None,
    ):
        """Initialize with DB pool for config and credential cache.

        Args:
            db_pool: Database pool for auth config and server credentials.
            cache: Credential validation cache (Redis or in-process).
            encryption_key: Optional Fernet key for encrypting stored credentials.
        """
        self._db_pool = db_pool
        self._cache = cache
        self._config = AuthConfig(
            auth_mode=AuthMode.BOTH,
            validate_credentials=True,
            valid_cache_ttl_seconds=3600,
            invalid_cache_ttl_seconds=300,
        )
        self._http_client: httpx.AsyncClient | None = None
        self._store = CredentialStore(db_pool, encryption_key) if db_pool else None
        # In-memory TTL cache for server credentials (avoids DB hit per resolve)
        self._server_key_cache: dict[str, tuple[float, Credential]] = {}
        self._server_key_ttl = 60.0  # seconds

    async def initialize(self, default_auth_mode: AuthMode = AuthMode.BOTH) -> None:
        """Load auth config from DB. Falls back to default on first boot."""
        if self._db_pool is None:
            logger.info("No DB pool - using default auth config")
            self._config = replace(self._config, auth_mode=default_auth_mode)
            return

        pool = await self._db_pool.get_pool()
        raw_row = await pool.fetchrow("SELECT * FROM auth_config WHERE id = 1")
        if raw_row:
            row: dict[str, Any] = dict(raw_row)
            self._config = AuthConfig(
                auth_mode=parse_auth_mode(row["auth_mode"]),
                validate_credentials=bool(row["validate_credentials"]),
                valid_cache_ttl_seconds=row["valid_cache_ttl_seconds"],
                invalid_cache_ttl_seconds=row["invalid_cache_ttl_seconds"],
                updated_at=str(row["updated_at"]) if row["updated_at"] else None,
                updated_by=row["updated_by"],
            )
        else:
            self._config = replace(self._config, auth_mode=default_auth_mode)

        logger.info(
            f"Auth config loaded: mode={self._config.auth_mode.value}, validate={self._config.validate_credentials}"
        )

    @property
    def config(self) -> AuthConfig:
        """Current auth configuration."""
        return self._config

    async def update_config(
        self,
        auth_mode: str | None = None,
        validate_credentials: bool | None = None,
        valid_cache_ttl_seconds: int | None = None,
        invalid_cache_ttl_seconds: int | None = None,
        updated_by: str = "api",
    ) -> AuthConfig:
        """Update auth config in DB and swap in-memory copy atomically.

        Builds a new AuthConfig and replaces the reference in one assignment,
        so concurrent readers always see a consistent snapshot (no torn reads).

        Note: the write path uses `AuthMode(auth_mode)` directly, not the
        legacy-tolerant `parse_auth_mode()` used by `initialize()`. This is
        intentional — the admin API already validates the incoming value at
        `admin/routes.py` before calling this method, and writing a pre-#524
        alias like `'proxy_key'` back into the DB would undo migration 013.
        Strictness here is load-bearing.
        """
        new_config = AuthConfig(
            auth_mode=AuthMode(auth_mode) if auth_mode is not None else self._config.auth_mode,
            validate_credentials=validate_credentials
            if validate_credentials is not None
            else self._config.validate_credentials,
            valid_cache_ttl_seconds=valid_cache_ttl_seconds
            if valid_cache_ttl_seconds is not None
            else self._config.valid_cache_ttl_seconds,
            invalid_cache_ttl_seconds=invalid_cache_ttl_seconds
            if invalid_cache_ttl_seconds is not None
            else self._config.invalid_cache_ttl_seconds,
            updated_by=updated_by,
        )

        if self._db_pool:
            pool = await self._db_pool.get_pool()
            await pool.execute(
                """
                INSERT INTO auth_config (id, auth_mode, validate_credentials,
                    valid_cache_ttl_seconds, invalid_cache_ttl_seconds,
                    updated_at, updated_by)
                VALUES (1, $1, $2, $3, $4, NOW(), $5)
                ON CONFLICT (id) DO UPDATE SET
                    auth_mode = EXCLUDED.auth_mode,
                    validate_credentials = EXCLUDED.validate_credentials,
                    valid_cache_ttl_seconds = EXCLUDED.valid_cache_ttl_seconds,
                    invalid_cache_ttl_seconds = EXCLUDED.invalid_cache_ttl_seconds,
                    updated_at = EXCLUDED.updated_at,
                    updated_by = EXCLUDED.updated_by
                """,
                new_config.auth_mode.value,
                new_config.validate_credentials,
                new_config.valid_cache_ttl_seconds,
                new_config.invalid_cache_ttl_seconds,
                updated_by,
            )

            ts_row = await pool.fetchrow("SELECT updated_at FROM auth_config WHERE id = 1")
            if ts_row:
                new_config = replace(new_config, updated_at=str(ts_row["updated_at"]))

        # Atomic swap — readers see old or new, never a mix
        self._config = new_config
        logger.info(f"Auth config updated: mode={new_config.auth_mode.value} by {updated_by}")
        return self._config

    async def validate_credential(self, credential: str, *, is_bearer: bool) -> bool:
        """Check if an Anthropic API key/token is valid.

        Checks cache first. On miss, calls the free count_tokens
        endpoint and caches the result.

        Args:
            credential: The API key or OAuth token value.
            is_bearer: True if the credential is a bearer/OAuth token
                       (send via Authorization header), False if it's an
                       API key (send via x-api-key header).
        """
        key_hash = hash_credential(credential)

        # Check cache
        cached = await self._get_cached(key_hash)
        if cached is not None:
            await self._touch_last_used(key_hash)
            return cached.valid

        # Cache miss - validate against Anthropic API
        is_oauth_bearer = is_bearer
        is_valid = await self._call_count_tokens(credential, is_bearer=is_bearer)
        if is_valid is None:
            # Inconclusive (network error, unexpected status, or OAuth bearer that
            # count_tokens can't validate). For OAuth tokens, pass through and let
            # Anthropic's messages endpoint decide. For API keys, block to be safe.
            return is_oauth_bearer
        await self._cache_result(key_hash, is_valid)
        logger.info(f"Credential validated: hash={key_hash[:16]}... valid={is_valid}")
        return is_valid

    async def on_backend_401(self, api_key: str) -> None:
        """Invalidate a credential that got rejected by the backend."""
        key_hash = hash_credential(api_key)
        await self._invalidate_key(key_hash)
        logger.info(f"Credential invalidated (backend 401): hash={key_hash[:16]}...")

    async def invalidate_credential(self, key_hash: str) -> bool:
        """Remove a cached credential by its hash. Returns True if found."""
        return await self._invalidate_key(key_hash)

    async def invalidate_all(self) -> int:
        """Remove all cached credentials. Returns count deleted."""
        if self._cache is None:
            return 0

        keys: list[str] = []
        async for key in self._cache.scan_iter(match=f"{CACHE_KEY_PREFIX}*"):
            keys.append(key)

        if not keys:
            return 0

        # Single UNLINK is non-blocking and handles the batch atomically
        return int(await self._cache.unlink(*keys))

    async def list_cached(self) -> list[CachedCredential]:
        """List all cached credentials (hashes and metadata only)."""
        if self._cache is None:
            return []

        results = []
        async for key in self._cache.scan_iter(match=f"{CACHE_KEY_PREFIX}*"):
            raw = await self._cache.get(key)
            if raw is None:
                continue
            key_str = key
            data = self._parse_cached_data(raw, key_str)
            if data is None:
                continue
            results.append(
                CachedCredential(
                    key_hash=key_str.removeprefix(CACHE_KEY_PREFIX),
                    valid=data["valid"],
                    validated_at=data["validated_at"],
                    last_used_at=data.get("last_used_at", data["validated_at"]),
                )
            )
        return results

    # --- Internal helpers ---

    def _parse_cached_data(self, raw: str, context: str) -> dict | None:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Corrupted cache entry in %s, ignoring", context)
            return None

    async def _get_cached(self, key_hash: str) -> CachedCredential | None:
        if self._cache is None:
            return None
        raw = await self._cache.get(f"{CACHE_KEY_PREFIX}{key_hash}")
        if raw is None:
            return None
        data = self._parse_cached_data(raw, f"{key_hash[:16]}...")
        if data is None:
            return None
        return CachedCredential(
            key_hash=key_hash,
            valid=data["valid"],
            validated_at=data["validated_at"],
            last_used_at=data.get("last_used_at", data["validated_at"]),
        )

    async def _cache_result(self, key_hash: str, valid: bool) -> None:
        if self._cache is None:
            return
        now = time.time()
        ttl = self._config.valid_cache_ttl_seconds if valid else self._config.invalid_cache_ttl_seconds
        data = json.dumps({"valid": valid, "validated_at": now, "last_used_at": now})
        await self._cache.setex(f"{CACHE_KEY_PREFIX}{key_hash}", ttl, data)

    async def _touch_last_used(self, key_hash: str) -> None:
        """Update last_used_at without resetting TTL.

        Best-effort: the key could expire between get() and setex().
        This only affects the last_used_at metadata, not auth decisions,
        so a missed update is harmless.
        """
        if self._cache is None:
            return
        redis_key = f"{CACHE_KEY_PREFIX}{key_hash}"
        raw = await self._cache.get(redis_key)
        if raw is None:
            return
        data = self._parse_cached_data(raw, f"{key_hash[:16]}...")
        if data is None:
            return
        data["last_used_at"] = time.time()
        ttl = await self._cache.ttl(redis_key)
        if ttl > 0:
            await self._cache.setex(redis_key, ttl, json.dumps(data))

    async def _invalidate_key(self, key_hash: str) -> bool:
        if self._cache is None:
            return False
        return await self._cache.delete(f"{CACHE_KEY_PREFIX}{key_hash}")

    async def _call_count_tokens(self, credential: str, *, is_bearer: bool) -> bool | None:
        """Validate a credential by calling the free count_tokens endpoint.

        Returns True/False for definitive results, None for inconclusive
        (network errors, unexpected status codes) so callers can avoid
        caching a bad result.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)

        # OAuth tokens arrive via Authorization: Bearer; API keys via x-api-key.
        headers: dict[str, str] = {
            "anthropic-version": ANTHROPIC_API_VERSION,
            "anthropic-beta": ANTHROPIC_BETA,
            "content-type": "application/json",
        }
        if is_bearer:
            headers["authorization"] = f"Bearer {credential}"
        else:
            headers["x-api-key"] = credential

        try:
            response = await self._http_client.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=VALIDATION_PAYLOAD,
            )
            if response.status_code == 200:
                return True
            if response.status_code == 401:
                if is_bearer:
                    # OAuth bearer tokens (e.g. from claude.ai) may be valid for the
                    # messages endpoint but rejected by count_tokens. Treat as inconclusive
                    # so the request is forwarded and Anthropic's real endpoint decides.
                    logger.debug("count_tokens returned 401 for bearer token; treating as inconclusive")
                    return None
                return False
            logger.warning(f"Credential validation got unexpected status: {response.status_code}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Credential validation network error: {repr(e)}")
            return None

    # ---- Auth Provider Resolution ----

    async def resolve(
        self,
        provider: AuthProvider,
        context: "PolicyContext",
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
                else:
                    logger.debug(
                        "No user credential on request, silently falling back to server key %r",
                        provider.name,
                    )
                return await self._get_server_key(provider.name)

        raise CredentialError(f"Unknown auth provider type: {type(provider)}")

    def _get_user_credential(self, context: "PolicyContext") -> Credential:
        """Read user credential from PolicyContext (set by gateway)."""
        if context.user_credential is None:
            raise CredentialError("No user credential on request context")
        return context.user_credential

    async def _get_server_key(self, name: str) -> Credential:
        if self._store is None:
            raise CredentialError("No credential store configured (no database)")

        # Check in-memory cache first
        cached = self._server_key_cache.get(name)
        if cached is not None:
            cached_at, cred = cached
            if time.time() - cached_at < self._server_key_ttl:
                return cred
            del self._server_key_cache[name]

        cred = await self._store.get(name)
        if cred is None:
            raise CredentialError(f"Server key '{name}' not found")
        self._server_key_cache[name] = (time.time(), cred)
        return cred

    # ---- Server Credential CRUD ----

    async def put_server_credential(self, name: str, credential: Credential) -> None:
        """Store or update a server credential."""
        if self._store is None:
            raise CredentialError("No credential store configured")
        await self._store.put(name, credential)
        self._server_key_cache.pop(name, None)
        logger.info("Server credential '%s' stored (platform=%s)", name, credential.platform)

    async def delete_server_credential(self, name: str) -> bool:
        """Delete a server credential. Returns True if it existed."""
        if self._store is None:
            raise CredentialError("No credential store configured")
        deleted = await self._store.delete(name)
        self._server_key_cache.pop(name, None)
        if deleted:
            logger.info("Server credential '%s' deleted", name)
        return deleted

    async def list_server_credentials(self) -> list[str]:
        """List stored server credential names (no values)."""
        if self._store is None:
            return []
        return await self._store.list_names()

    async def close(self) -> None:
        """Clean up HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None


__all__ = [
    "AuthConfig",
    "AuthMode",
    "CachedCredential",
    "CredentialManager",
    "hash_credential",
]
