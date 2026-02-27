"""Unit tests for credential manager."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
        result = await manager.validate_credential("test-key", is_bearer=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_cache_miss_calls_api(self):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)

        with patch.object(manager, "_call_count_tokens", new_callable=AsyncMock, return_value=True):
            result = await manager.validate_credential("test-key", is_bearer=False)
            assert result is True
            mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_credential_cached_with_short_ttl(self):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)

        with patch.object(manager, "_call_count_tokens", new_callable=AsyncMock, return_value=False):
            result = await manager.validate_credential("bad-key", is_bearer=False)
            assert result is False
            call_args = mock_redis.setex.call_args
            ttl_arg = call_args[0][1]
            assert ttl_arg == 300  # invalid_cache_ttl_seconds default

    @pytest.mark.asyncio
    async def test_network_error_not_cached(self):
        """Network errors should not cache a result, so the next request retries."""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)

        with patch.object(manager, "_call_count_tokens", new_callable=AsyncMock, return_value=None):
            result = await manager.validate_credential("some-key", is_bearer=False)
            assert result is False
            mock_redis.setex.assert_not_called()


class TestCallCountTokens:
    @pytest.mark.asyncio
    async def test_200_returns_true(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("valid-key", is_bearer=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_401_returns_false(self):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("invalid-key", is_bearer=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("some-key", is_bearer=False)
        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_status_returns_none(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("some-key", is_bearer=False)
        assert result is None

    @pytest.mark.asyncio
    async def test_api_key_sends_x_api_key_header(self):
        """API keys (sk-ant-*) should be sent via x-api-key header."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("sk-ant-api03-abc123", is_bearer=False)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["x-api-key"] == "sk-ant-api03-abc123"
        assert "authorization" not in headers

    @pytest.mark.asyncio
    async def test_bearer_token_sends_authorization_header(self):
        """Bearer credentials should be sent via Authorization: Bearer header."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("eyJhbGciOiJSUz.oauth-token", is_bearer=True)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["authorization"] == "Bearer eyJhbGciOiJSUz.oauth-token"
        assert "x-api-key" not in headers

    @pytest.mark.asyncio
    async def test_bearer_api_key_uses_x_api_key_header(self):
        """Anthropic API keys sent as Bearer should still use x-api-key upstream."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("sk-ant-api03-abc123", is_bearer=True)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["x-api-key"] == "sk-ant-api03-abc123"
        assert "authorization" not in headers

    @pytest.mark.asyncio
    async def test_bearer_token_includes_oauth_beta_header(self):
        """Bearer tokens should include the OAuth beta flag in anthropic-beta."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("oauth-token-xyz", is_bearer=True)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "oauth-2025-04-20" in headers["anthropic-beta"]

    @pytest.mark.asyncio
    async def test_api_key_excludes_oauth_beta_header(self):
        """API keys should NOT include the OAuth beta flag in anthropic-beta."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("sk-ant-api03-abc123", is_bearer=False)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "oauth-2025-04-20" not in headers["anthropic-beta"]

    @pytest.mark.asyncio
    async def test_bearer_api_key_excludes_oauth_beta_header(self):
        """Anthropic API keys in Bearer form should not use OAuth beta header."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, redis_client=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("sk-ant-api03-abc123", is_bearer=True)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "oauth-2025-04-20" not in headers["anthropic-beta"]


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


class TestListCached:
    @pytest.mark.asyncio
    async def test_returns_empty_without_redis(self):
        manager = CredentialManager(db_pool=None, redis_client=None)
        result = await manager.list_cached()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_cached_entries(self):
        now = time.time()
        entry = json.dumps({"valid": True, "validated_at": now, "last_used_at": now})
        mock_redis = AsyncMock()

        async def fake_scan_iter(match=None):
            yield b"luthien:auth:cred:abc123"
            yield b"luthien:auth:cred:def456"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value=entry.encode())

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)
        result = await manager.list_cached()
        assert len(result) == 2
        assert result[0].key_hash == "abc123"
        assert result[0].valid is True

    @pytest.mark.asyncio
    async def test_skips_expired_keys(self):
        """Keys that expire between scan and get are skipped."""
        mock_redis = AsyncMock()

        async def fake_scan_iter(match=None):
            yield b"luthien:auth:cred:gone"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.get = AsyncMock(return_value=None)

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)
        result = await manager.list_cached()
        assert result == []


class TestInvalidateAll:
    @pytest.mark.asyncio
    async def test_returns_zero_without_redis(self):
        manager = CredentialManager(db_pool=None, redis_client=None)
        result = await manager.invalidate_all()
        assert result == 0

    @pytest.mark.asyncio
    async def test_deletes_all_matching_keys(self):
        mock_redis = AsyncMock()

        async def fake_scan_iter(match=None):
            yield b"luthien:auth:cred:abc"
            yield b"luthien:auth:cred:def"

        mock_redis.scan_iter = fake_scan_iter
        mock_redis.unlink = AsyncMock(return_value=2)

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)
        result = await manager.invalidate_all()
        assert result == 2
        mock_redis.unlink.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_keys_returns_zero(self):
        mock_redis = AsyncMock()

        async def fake_scan_iter(match=None):
            return
            yield  # make it an async generator

        mock_redis.scan_iter = fake_scan_iter

        manager = CredentialManager(db_pool=None, redis_client=mock_redis)
        result = await manager.invalidate_all()
        assert result == 0
        mock_redis.unlink.assert_not_called()


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
