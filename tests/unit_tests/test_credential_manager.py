"""Unit tests for credential manager."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.credential_manager import (
    AuthMode,
    CredentialManager,
    hash_credential,
)


class TestHashCredential:
    def test_deterministic(self):
        assert hash_credential("key1") == hash_credential("key1")

    def test_different_keys_different_hashes(self):
        assert hash_credential("key1") != hash_credential("key2")

    def test_sha256_length(self):
        assert len(hash_credential("key1")) == 64


class TestCredentialManagerInit:
    def test_default_config(self):
        manager = CredentialManager(db_pool=None, redis_client=None)
        assert manager.config.auth_mode == AuthMode.PROXY_KEY
        assert manager.config.validate_credentials is True
        assert manager.config.valid_cache_ttl_seconds == 3600
        assert manager.config.invalid_cache_ttl_seconds == 300


class TestCredentialManagerInitialize:
    @pytest.mark.asyncio
    async def test_no_db_uses_default(self):
        manager = CredentialManager(db_pool=None, redis_client=None)
        await manager.initialize(default_auth_mode="passthrough")
        assert manager.config.auth_mode == AuthMode.PASSTHROUGH

    @pytest.mark.asyncio
    async def test_loads_from_db(self):
        mock_pool = AsyncMock()
        mock_pool.fetchrow.return_value = {
            "auth_mode": "both",
            "validate_credentials": False,
            "valid_cache_ttl_seconds": 7200,
            "invalid_cache_ttl_seconds": 600,
            "updated_at": "2024-01-01 00:00:00",
            "updated_by": "admin",
        }
        mock_db = AsyncMock()
        mock_db.get_pool.return_value = mock_pool

        manager = CredentialManager(db_pool=mock_db, redis_client=None)
        await manager.initialize()
        assert manager.config.auth_mode == AuthMode.BOTH
        assert manager.config.validate_credentials is False
        assert manager.config.valid_cache_ttl_seconds == 7200

    @pytest.mark.asyncio
    async def test_no_row_uses_default(self):
        mock_pool = AsyncMock()
        mock_pool.fetchrow.return_value = None
        mock_db = AsyncMock()
        mock_db.get_pool.return_value = mock_pool

        manager = CredentialManager(db_pool=mock_db, redis_client=None)
        await manager.initialize(default_auth_mode="passthrough")
        assert manager.config.auth_mode == AuthMode.PASSTHROUGH


class TestValidateCredential:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self):
        mock_redis = AsyncMock()
        cached_data = json.dumps({"valid": True, "validated_at": time.time(), "last_used_at": time.time()})
        mock_redis.get.return_value = cached_data.encode()
        mock_redis.ttl.return_value = 3000

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)
        result = await manager.validate_credential("test-key")
        assert result is True

    @pytest.mark.asyncio
    async def test_cache_miss_calls_api(self):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)

        with patch.object(manager, "_call_count_tokens", new_callable=AsyncMock, return_value=True):
            result = await manager.validate_credential("test-key")
            assert result is True
            mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_credential_cached_with_short_ttl(self):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)

        with patch.object(manager, "_call_count_tokens", new_callable=AsyncMock, return_value=False):
            result = await manager.validate_credential("bad-key")
            assert result is False
            call_args = mock_redis.setex.call_args
            ttl_arg = call_args[0][1]
            assert ttl_arg == 300  # invalid_cache_ttl_seconds default


class TestCallCountTokens:
    @pytest.mark.asyncio
    async def test_200_returns_true(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("valid-key")
        assert result is True

    @pytest.mark.asyncio
    async def test_401_returns_false(self):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("invalid-key")
        assert result is False


class TestInvalidation:
    @pytest.mark.asyncio
    async def test_invalidate_credential(self):
        mock_redis = AsyncMock()
        mock_redis.delete.return_value = 1
        manager = CredentialManager(db_pool=None, redis_client=mock_redis)
        result = await manager.invalidate_credential("somehash")
        assert result is True

    @pytest.mark.asyncio
    async def test_invalidate_missing_credential(self):
        mock_redis = AsyncMock()
        mock_redis.delete.return_value = 0
        manager = CredentialManager(db_pool=None, redis_client=mock_redis)
        result = await manager.invalidate_credential("nothash")
        assert result is False

    @pytest.mark.asyncio
    async def test_on_backend_401(self):
        mock_redis = AsyncMock()
        mock_redis.delete.return_value = 1
        manager = CredentialManager(db_pool=None, redis_client=mock_redis)
        await manager.on_backend_401("some-api-key")
        expected_hash = hash_credential("some-api-key")
        mock_redis.delete.assert_called_once_with(f"luthien:auth:cred:{expected_hash}")


class TestUpdateConfig:
    @pytest.mark.asyncio
    async def test_update_without_db(self):
        manager = CredentialManager(db_pool=None, redis_client=None)
        config = await manager.update_config(auth_mode="passthrough")
        assert config.auth_mode == AuthMode.PASSTHROUGH

    @pytest.mark.asyncio
    async def test_partial_update(self):
        manager = CredentialManager(db_pool=None, redis_client=None)
        config = await manager.update_config(validate_credentials=False)
        assert config.validate_credentials is False
        assert config.auth_mode == AuthMode.PROXY_KEY  # unchanged
