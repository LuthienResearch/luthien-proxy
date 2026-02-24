"""Credential validation and caching for passthrough authentication.

Manages configurable auth modes (proxy_key, passthrough, both) and validates
Anthropic credentials via the free count_tokens endpoint, caching results in Redis.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
from redis.asyncio import Redis

from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)

REDIS_KEY_PREFIX = "luthien:auth:cred:"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages/count_tokens"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_BETA = "token-counting-2024-11-01"
ANTHROPIC_OAUTH_BETA = "token-counting-2024-11-01,oauth-2025-04-20"

# Minimal payload for credential validation (free endpoint).
# OAuth tokens have access to different models than API keys, so we use
# a model available to both: claude-sonnet-4-20250514.
VALIDATION_MODEL = "claude-sonnet-4-20250514"
VALIDATION_PAYLOAD = {
    "model": VALIDATION_MODEL,
    "messages": [{"role": "user", "content": "hi"}],
}


class AuthMode(str, Enum):
    """Authentication mode for the gateway."""

    PROXY_KEY = "proxy_key"
    PASSTHROUGH = "passthrough"
    BOTH = "both"


@dataclass
class AuthConfig:
    """Current auth configuration (loaded from DB)."""

    auth_mode: AuthMode
    validate_credentials: bool
    valid_cache_ttl_seconds: int
    invalid_cache_ttl_seconds: int
    updated_at: str | None = None
    updated_by: str | None = None


@dataclass
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
    Credential validation results are cached in Redis with configurable TTLs.
    """

    def __init__(self, db_pool: DatabasePool | None, redis_client: Redis | None):
        """Initialize with DB pool for config and Redis for credential cache."""
        self._db_pool = db_pool
        self._redis = redis_client
        self._config = AuthConfig(
            auth_mode=AuthMode.PROXY_KEY,
            validate_credentials=True,
            valid_cache_ttl_seconds=3600,
            invalid_cache_ttl_seconds=300,
        )
        self._http_client: httpx.AsyncClient | None = None

    async def initialize(self, default_auth_mode: str = "both") -> None:
        """Load auth config from DB. Falls back to default on first boot."""
        if self._db_pool is None:
            logger.info("No DB pool - using default auth config")
            self._config.auth_mode = AuthMode(default_auth_mode)
            return

        pool = await self._db_pool.get_pool()
        raw_row = await pool.fetchrow("SELECT * FROM auth_config WHERE id = 1")
        if raw_row:
            row: dict[str, Any] = dict(raw_row)
            self._config = AuthConfig(
                auth_mode=AuthMode(row["auth_mode"]),
                validate_credentials=row["validate_credentials"],
                valid_cache_ttl_seconds=row["valid_cache_ttl_seconds"],
                invalid_cache_ttl_seconds=row["invalid_cache_ttl_seconds"],
                updated_at=str(row["updated_at"]) if row["updated_at"] else None,
                updated_by=row["updated_by"],
            )
        else:
            self._config.auth_mode = AuthMode(default_auth_mode)

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
        """Update auth config in DB and refresh in-memory copy."""
        if auth_mode is not None:
            self._config.auth_mode = AuthMode(auth_mode)
        if validate_credentials is not None:
            self._config.validate_credentials = validate_credentials
        if valid_cache_ttl_seconds is not None:
            self._config.valid_cache_ttl_seconds = valid_cache_ttl_seconds
        if invalid_cache_ttl_seconds is not None:
            self._config.invalid_cache_ttl_seconds = invalid_cache_ttl_seconds
        self._config.updated_by = updated_by

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
                self._config.auth_mode.value,
                self._config.validate_credentials,
                self._config.valid_cache_ttl_seconds,
                self._config.invalid_cache_ttl_seconds,
                updated_by,
            )

            ts_row = await pool.fetchrow("SELECT updated_at FROM auth_config WHERE id = 1")
            if ts_row:
                self._config.updated_at = str(ts_row["updated_at"])

        logger.info(f"Auth config updated: mode={self._config.auth_mode.value} by {updated_by}")
        return self._config

    async def validate_credential(self, credential: str, *, is_bearer: bool) -> bool:
        """Check if an Anthropic API key/token is valid.

        Checks Redis cache first. On miss, calls the free count_tokens
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
        is_valid = await self._call_count_tokens(credential, is_bearer=is_bearer)
        if is_valid is None:
            # Inconclusive (network error, unexpected status) -- don't cache,
            # so the next request retries instead of locking out a valid key.
            return False
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
        if self._redis is None:
            return 0

        keys: list[bytes | str] = []
        async for key in self._redis.scan_iter(match=f"{REDIS_KEY_PREFIX}*"):
            keys.append(key)

        if not keys:
            return 0

        # Single UNLINK is non-blocking and handles the batch atomically
        return int(await self._redis.unlink(*keys))

    async def list_cached(self) -> list[CachedCredential]:
        """List all cached credentials (hashes and metadata only)."""
        if self._redis is None:
            return []

        results = []
        async for key in self._redis.scan_iter(match=f"{REDIS_KEY_PREFIX}*"):
            raw = await self._redis.get(key)
            if raw is None:
                continue
            key_str = key if isinstance(key, str) else key.decode()
            data = self._parse_cached_data(raw, key_str)
            if data is None:
                continue
            results.append(
                CachedCredential(
                    key_hash=key_str.removeprefix(REDIS_KEY_PREFIX),
                    valid=data["valid"],
                    validated_at=data["validated_at"],
                    last_used_at=data.get("last_used_at", data["validated_at"]),
                )
            )
        return results

    # --- Internal helpers ---

    def _parse_cached_data(self, raw: str | bytes, context: str) -> dict | None:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Corrupted cache entry in %s, ignoring", context)
            return None

    async def _get_cached(self, key_hash: str) -> CachedCredential | None:
        if self._redis is None:
            return None
        raw = await self._redis.get(f"{REDIS_KEY_PREFIX}{key_hash}")
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
        if self._redis is None:
            return
        now = time.time()
        ttl = self._config.valid_cache_ttl_seconds if valid else self._config.invalid_cache_ttl_seconds
        data = json.dumps({"valid": valid, "validated_at": now, "last_used_at": now})
        await self._redis.setex(f"{REDIS_KEY_PREFIX}{key_hash}", ttl, data)

    async def _touch_last_used(self, key_hash: str) -> None:
        """Update last_used_at without resetting TTL.

        Best-effort: the key could expire between get() and setex().
        This only affects the last_used_at metadata, not auth decisions,
        so a missed update is harmless.
        """
        if self._redis is None:
            return
        redis_key = f"{REDIS_KEY_PREFIX}{key_hash}"
        raw = await self._redis.get(redis_key)
        if raw is None:
            return
        data = self._parse_cached_data(raw, f"{key_hash[:16]}...")
        if data is None:
            return
        data["last_used_at"] = time.time()
        ttl = await self._redis.ttl(redis_key)
        if ttl > 0:
            await self._redis.setex(redis_key, ttl, json.dumps(data))

    async def _invalidate_key(self, key_hash: str) -> bool:
        if self._redis is None:
            return False
        result = await self._redis.delete(f"{REDIS_KEY_PREFIX}{key_hash}")
        return result > 0

    async def _call_count_tokens(self, credential: str, *, is_bearer: bool) -> bool | None:
        """Validate a credential by calling the free count_tokens endpoint.

        Returns True/False for definitive results, None for inconclusive
        (network errors, unexpected status codes) so callers can avoid
        caching a bad result.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)

        beta = ANTHROPIC_OAUTH_BETA if is_bearer else ANTHROPIC_BETA
        headers: dict[str, str] = {
            "anthropic-version": ANTHROPIC_API_VERSION,
            "anthropic-beta": beta,
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
                return False
            logger.warning(f"Credential validation got unexpected status: {response.status_code}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Credential validation network error: {e}")
            return None

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
