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
        manager = CredentialManager(db_pool=None, cache=None)
        assert manager.config.auth_mode == AuthMode.BOTH
        assert manager.config.validate_credentials is True
        assert manager.config.valid_cache_ttl_seconds == 3600
        assert manager.config.invalid_cache_ttl_seconds == 300


class TestCredentialManagerInitialize:
    @pytest.mark.asyncio
    async def test_no_db_uses_default(self):
        manager = CredentialManager(db_pool=None, cache=None)
        await manager.initialize(default_auth_mode=AuthMode.PASSTHROUGH)
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

        manager = CredentialManager(db_pool=mock_db, cache=None)
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

        manager = CredentialManager(db_pool=mock_db, cache=None)
        await manager.initialize(default_auth_mode=AuthMode.PASSTHROUGH)
        assert manager.config.auth_mode == AuthMode.PASSTHROUGH

    @pytest.mark.asyncio
    async def test_loads_client_key_row(self):
        """Regression guard: a row with the post-#524 'client_key' value round-trips.

        Closes the test gap flagged in review — verifies that after migration
        013 rewrites `auth_mode='proxy_key'` → `'client_key'`, the gateway can
        still parse its own stored config.
        """
        mock_pool = AsyncMock()
        mock_pool.fetchrow.return_value = {
            "auth_mode": "client_key",
            "validate_credentials": True,
            "valid_cache_ttl_seconds": 3600,
            "invalid_cache_ttl_seconds": 300,
            "updated_at": None,
            "updated_by": None,
        }
        mock_db = AsyncMock()
        mock_db.get_pool.return_value = mock_pool

        manager = CredentialManager(db_pool=mock_db, cache=None)
        await manager.initialize()
        assert manager.config.auth_mode == AuthMode.CLIENT_KEY

    @pytest.mark.asyncio
    async def test_tolerates_legacy_proxy_key_row(self, caplog):
        """Regression guard: a pre-#524 'proxy_key' row does not crash startup.

        Postgres migrations run in a separate service from the gateway. If the
        gateway restarts before migration 013 has applied, the DB still holds
        'proxy_key' — which is no longer a valid AuthMode value. We tolerate
        it with a warning so the gateway can still serve traffic.
        """
        mock_pool = AsyncMock()
        mock_pool.fetchrow.return_value = {
            "auth_mode": "proxy_key",
            "validate_credentials": True,
            "valid_cache_ttl_seconds": 3600,
            "invalid_cache_ttl_seconds": 300,
            "updated_at": None,
            "updated_by": None,
        }
        mock_db = AsyncMock()
        mock_db.get_pool.return_value = mock_pool

        manager = CredentialManager(db_pool=mock_db, cache=None)
        with caplog.at_level("WARNING", logger="luthien_proxy.credential_manager"):
            await manager.initialize()
        assert manager.config.auth_mode == AuthMode.CLIENT_KEY
        assert any("pre-PR-#524" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_rejects_unknown_auth_mode_row(self):
        """An auth_mode value that is neither current nor a legacy alias should still raise."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow.return_value = {
            "auth_mode": "not_a_real_mode",
            "validate_credentials": True,
            "valid_cache_ttl_seconds": 3600,
            "invalid_cache_ttl_seconds": 300,
            "updated_at": None,
            "updated_by": None,
        }
        mock_db = AsyncMock()
        mock_db.get_pool.return_value = mock_pool

        manager = CredentialManager(db_pool=mock_db, cache=None)
        with pytest.raises(ValueError):
            await manager.initialize()


class TestValidateCredential:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self):
        mock_redis = AsyncMock()
        cached_data = json.dumps({"valid": True, "validated_at": time.time(), "last_used_at": time.time()})
        mock_redis.get.return_value = cached_data.encode()
        mock_redis.ttl.return_value = 3000

        manager = CredentialManager(db_pool=None, cache=mock_redis)
        result = await manager.validate_credential("test-key", is_bearer=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_cache_miss_calls_api(self):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = CredentialManager(db_pool=None, cache=mock_redis)

        with patch.object(manager, "_call_count_tokens", new_callable=AsyncMock, return_value=True):
            result = await manager.validate_credential("test-key", is_bearer=False)
            assert result is True
            mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_credential_cached_with_short_ttl(self):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = CredentialManager(db_pool=None, cache=mock_redis)

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

        manager = CredentialManager(db_pool=None, cache=mock_redis)

        with patch.object(manager, "_call_count_tokens", new_callable=AsyncMock, return_value=None):
            result = await manager.validate_credential("some-key", is_bearer=False)
            assert result is False
            mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_inconclusive_passthrough_for_oauth_bearer(self):
        """When inconclusive AND credential is OAuth bearer, validate_credential returns True (pass through)."""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = CredentialManager(db_pool=None, cache=mock_redis)

        with patch.object(manager, "_call_count_tokens", new_callable=AsyncMock, return_value=None):
            result = await manager.validate_credential("oauth-token-xyz", is_bearer=True)
            assert result is True
            mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_inconclusive_blocks_api_key(self):
        """When inconclusive AND credential is API key, validate_credential returns False (block)."""
        mock_redis = AsyncMock()
        mock_redis.get.return_value = None

        manager = CredentialManager(db_pool=None, cache=mock_redis)

        with patch.object(manager, "_call_count_tokens", new_callable=AsyncMock, return_value=None):
            result = await manager.validate_credential("sk-ant-api03-abc123", is_bearer=False)
            assert result is False
            mock_redis.setex.assert_not_called()


class TestCallCountTokens:
    @pytest.mark.asyncio
    async def test_200_returns_true(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, cache=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("valid-key", is_bearer=False)
        assert result is True

    @pytest.mark.asyncio
    async def test_401_returns_false(self):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, cache=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("invalid-key", is_bearer=False)
        assert result is False

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        manager = CredentialManager(db_pool=None, cache=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("some-key", is_bearer=False)
        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_status_returns_none(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, cache=None)
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

        manager = CredentialManager(db_pool=None, cache=None)
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

        manager = CredentialManager(db_pool=None, cache=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("eyJhbGciOiJSUz.oauth-token", is_bearer=True)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["authorization"] == "Bearer eyJhbGciOiJSUz.oauth-token"
        assert "x-api-key" not in headers

    @pytest.mark.asyncio
    async def test_bearer_credential_uses_authorization_header(self):
        """All Bearer credentials (regardless of format) are sent via Authorization: Bearer.
        Transport (header) is the authority, not token prefix."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, cache=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("sk-ant-api03-abc123", is_bearer=True)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["authorization"] == "Bearer sk-ant-api03-abc123"
        assert "x-api-key" not in headers

    @pytest.mark.asyncio
    async def test_bearer_token_includes_token_counting_beta_header(self):
        """Bearer tokens should include the token counting beta flag in anthropic-beta."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, cache=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("oauth-token-xyz", is_bearer=True)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["anthropic-beta"] == "token-counting-2024-11-01"

    @pytest.mark.asyncio
    async def test_api_key_excludes_oauth_beta_header(self):
        """API keys should NOT include oauth flags in anthropic-beta."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, cache=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("sk-ant-api03-abc123", is_bearer=False)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["anthropic-beta"] == "token-counting-2024-11-01"

    @pytest.mark.asyncio
    async def test_bearer_credential_includes_token_counting_beta_header(self):
        """All Bearer credentials include token counting beta header.
        Transport (header) is the authority, not token prefix."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, cache=None)
        manager._http_client = mock_client
        await manager._call_count_tokens("sk-ant-api03-abc123", is_bearer=True)

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["anthropic-beta"] == "token-counting-2024-11-01"

    @pytest.mark.asyncio
    async def test_401_bearer_token_returns_none(self):
        """OAuth bearer tokens (non-API-key) that get 401 from count_tokens return None (inconclusive)."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        manager = CredentialManager(db_pool=None, cache=None)
        manager._http_client = mock_client
        result = await manager._call_count_tokens("oauth-token-xyz", is_bearer=True)
        assert result is None


class TestInvalidation:
    @pytest.mark.asyncio
    async def test_invalidate_credential(self):
        mock_cache = AsyncMock()
        mock_cache.delete.return_value = True
        manager = CredentialManager(db_pool=None, cache=mock_cache)
        result = await manager.invalidate_credential("somehash")
        assert result is True

    @pytest.mark.asyncio
    async def test_invalidate_missing_credential(self):
        mock_cache = AsyncMock()
        mock_cache.delete.return_value = False
        manager = CredentialManager(db_pool=None, cache=mock_cache)
        result = await manager.invalidate_credential("nothash")
        assert result is False

    @pytest.mark.asyncio
    async def test_on_backend_401(self):
        mock_redis = AsyncMock()
        mock_redis.delete.return_value = 1
        manager = CredentialManager(db_pool=None, cache=mock_redis)
        await manager.on_backend_401("some-api-key")
        expected_hash = hash_credential("some-api-key")
        mock_redis.delete.assert_called_once_with(f"luthien:auth:cred:{expected_hash}")


class TestListCached:
    @pytest.mark.asyncio
    async def test_returns_empty_without_redis(self):
        manager = CredentialManager(db_pool=None, cache=None)
        result = await manager.list_cached()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_cached_entries(self):
        now = time.time()
        entry = json.dumps({"valid": True, "validated_at": now, "last_used_at": now})
        mock_cache = AsyncMock()

        async def fake_scan_iter(match=None):
            yield "luthien:auth:cred:abc123"
            yield "luthien:auth:cred:def456"

        mock_cache.scan_iter = fake_scan_iter
        mock_cache.get = AsyncMock(return_value=entry)

        manager = CredentialManager(db_pool=None, cache=mock_cache)
        result = await manager.list_cached()
        assert len(result) == 2
        assert result[0].key_hash == "abc123"
        assert result[0].valid is True

    @pytest.mark.asyncio
    async def test_skips_expired_keys(self):
        """Keys that expire between scan and get are skipped."""
        mock_cache = AsyncMock()

        async def fake_scan_iter(match=None):
            yield "luthien:auth:cred:gone"

        mock_cache.scan_iter = fake_scan_iter
        mock_cache.get = AsyncMock(return_value=None)

        manager = CredentialManager(db_pool=None, cache=mock_cache)
        result = await manager.list_cached()
        assert result == []


class TestInvalidateAll:
    @pytest.mark.asyncio
    async def test_returns_zero_without_redis(self):
        manager = CredentialManager(db_pool=None, cache=None)
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

        manager = CredentialManager(db_pool=None, cache=mock_redis)
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

        manager = CredentialManager(db_pool=None, cache=mock_redis)
        result = await manager.invalidate_all()
        assert result == 0
        mock_redis.unlink.assert_not_called()


class TestUpdateConfig:
    @pytest.mark.asyncio
    async def test_update_without_db(self):
        manager = CredentialManager(db_pool=None, cache=None)
        config = await manager.update_config(auth_mode="passthrough")
        assert config.auth_mode == AuthMode.PASSTHROUGH

    @pytest.mark.asyncio
    async def test_partial_update(self):
        manager = CredentialManager(db_pool=None, cache=None)
        config = await manager.update_config(validate_credentials=False)
        assert config.validate_credentials is False
        assert config.auth_mode == AuthMode.BOTH  # unchanged from default
