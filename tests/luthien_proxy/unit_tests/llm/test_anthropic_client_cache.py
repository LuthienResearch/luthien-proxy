"""Tests for AnthropicClient LRU cache.

Verifies that passthrough requests reuse cached client instances
instead of creating a new AnthropicClient (and underlying TCP connection)
per request.
"""

import asyncio
from unittest.mock import patch

import pytest

from luthien_proxy.llm import anthropic_client_cache
from luthien_proxy.llm.anthropic_client import AnthropicClient


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure each test starts with an empty cache."""
    anthropic_client_cache.clear()
    yield
    anthropic_client_cache.clear()


class TestGetClient:
    """Core cache behavior: get-or-create with key isolation."""

    async def test_returns_anthropic_client(self):
        client = await anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key")
        assert isinstance(client, AnthropicClient)

    async def test_same_credential_returns_same_instance(self):
        c1 = await anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key")
        c2 = await anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key")
        assert c1 is c2

    async def test_different_credentials_return_different_instances(self):
        c1 = await anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key")
        c2 = await anthropic_client_cache.get_client("sk-ant-api03-key2", auth_type="api_key")
        assert c1 is not c2

    async def test_different_auth_types_return_different_instances(self):
        """api_key and auth_token for the same credential are separate cache entries."""
        c1 = await anthropic_client_cache.get_client("token-abc", auth_type="api_key")
        c2 = await anthropic_client_cache.get_client("token-abc", auth_type="auth_token")
        assert c1 is not c2

    async def test_different_base_urls_return_different_instances(self):
        c1 = await anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key", base_url=None)
        c2 = await anthropic_client_cache.get_client(
            "sk-ant-api03-key1", auth_type="api_key", base_url="https://custom.api.com"
        )
        assert c1 is not c2

    async def test_same_base_url_returns_same_instance(self):
        c1 = await anthropic_client_cache.get_client(
            "sk-ant-api03-key1", auth_type="api_key", base_url="https://custom.api.com"
        )
        c2 = await anthropic_client_cache.get_client(
            "sk-ant-api03-key1", auth_type="api_key", base_url="https://custom.api.com"
        )
        assert c1 is c2

    async def test_api_key_auth_type_passes_api_key_kwarg(self):
        """Clients created with auth_type='api_key' use the api_key constructor param."""
        with patch.object(anthropic_client_cache, "AnthropicClient") as mock_cls:
            mock_cls.return_value = AnthropicClient(api_key="dummy")
            await anthropic_client_cache.get_client("the-key", auth_type="api_key", base_url="https://x.com")
            mock_cls.assert_called_once_with(api_key="the-key", base_url="https://x.com")

    async def test_auth_token_auth_type_passes_auth_token_kwarg(self):
        """Clients created with auth_type='auth_token' use the auth_token constructor param."""
        with patch.object(anthropic_client_cache, "AnthropicClient") as mock_cls:
            mock_cls.return_value = AnthropicClient(api_key="dummy")
            await anthropic_client_cache.get_client("oauth-tok", auth_type="auth_token", base_url=None)
            mock_cls.assert_called_once_with(auth_token="oauth-tok", base_url=None)


class TestLRUEviction:
    """Cache respects max size and evicts least-recently-used entries."""

    async def test_evicts_oldest_when_full(self):
        max_size = anthropic_client_cache.MAX_CACHE_SIZE
        clients = []
        for i in range(max_size):
            c = await anthropic_client_cache.get_client(f"key-{i}", auth_type="api_key")
            clients.append(c)
        assert anthropic_client_cache.cache_size() == max_size

        new_client = await anthropic_client_cache.get_client("key-overflow", auth_type="api_key")
        assert anthropic_client_cache.cache_size() == max_size
        assert new_client is not clients[0]

        refetched = await anthropic_client_cache.get_client("key-0", auth_type="api_key")
        assert refetched is not clients[0]

    async def test_eviction_calls_close_on_evicted_client(self):
        closed = []

        async def fake_close(self):
            closed.append(id(self))

        with patch.object(AnthropicClient, "close", fake_close):
            max_size = anthropic_client_cache.MAX_CACHE_SIZE
            for i in range(max_size):
                await anthropic_client_cache.get_client(f"key-{i}", auth_type="api_key")

            await anthropic_client_cache.get_client("key-overflow", auth_type="api_key")

            # Drain background tasks so fake_close runs before the patch exits
            if anthropic_client_cache._background_tasks:
                await asyncio.gather(*list(anthropic_client_cache._background_tasks), return_exceptions=True)

        assert len(closed) == 1

    async def test_cache_hit_refreshes_lru_position(self):
        max_size = anthropic_client_cache.MAX_CACHE_SIZE
        for i in range(max_size):
            await anthropic_client_cache.get_client(f"key-{i}", auth_type="api_key")

        refreshed = await anthropic_client_cache.get_client("key-0", auth_type="api_key")

        await anthropic_client_cache.get_client("key-overflow", auth_type="api_key")

        assert await anthropic_client_cache.get_client("key-0", auth_type="api_key") is refreshed
        assert anthropic_client_cache.cache_size() == max_size


class TestCacheManagement:
    """clear() and cache_size() helpers."""

    async def test_clear_empties_cache(self):
        await anthropic_client_cache.get_client("key-1", auth_type="api_key")
        await anthropic_client_cache.get_client("key-2", auth_type="api_key")
        assert anthropic_client_cache.cache_size() == 2

        count = anthropic_client_cache.clear()
        assert count == 2
        assert anthropic_client_cache.cache_size() == 0

    async def test_clear_on_empty_returns_zero(self):
        assert anthropic_client_cache.clear() == 0

    async def test_cache_size_tracks_entries(self):
        assert anthropic_client_cache.cache_size() == 0
        await anthropic_client_cache.get_client("key-1", auth_type="api_key")
        assert anthropic_client_cache.cache_size() == 1
        await anthropic_client_cache.get_client("key-2", auth_type="auth_token")
        assert anthropic_client_cache.cache_size() == 2

    async def test_cache_size_does_not_count_hits(self):
        """Accessing the same key multiple times doesn't increase size."""
        await anthropic_client_cache.get_client("key-1", auth_type="api_key")
        await anthropic_client_cache.get_client("key-1", auth_type="api_key")
        await anthropic_client_cache.get_client("key-1", auth_type="api_key")
        assert anthropic_client_cache.cache_size() == 1

    async def test_close_all_closes_clients_and_clears_cache(self):
        closed = []

        async def fake_close(self):
            closed.append(id(self))

        with patch.object(AnthropicClient, "close", fake_close):
            await anthropic_client_cache.get_client("key-1", auth_type="api_key")
            await anthropic_client_cache.get_client("key-2", auth_type="api_key")
            assert anthropic_client_cache.cache_size() == 2

            count = await anthropic_client_cache.close_all()

        assert count == 2
        assert anthropic_client_cache.cache_size() == 0
        assert len(closed) == 2

    async def test_close_all_on_empty_returns_zero(self):
        assert await anthropic_client_cache.close_all() == 0


class TestMaxCacheSizeConfigurable:
    """MAX_CACHE_SIZE can be overridden via ANTHROPIC_CLIENT_CACHE_SIZE env var."""

    def test_default_cache_size(self):
        assert anthropic_client_cache._DEFAULT_MAX_CACHE_SIZE == 16

    def test_env_var_overrides_cache_size(self, monkeypatch):
        """Reloading the module with the env var set changes MAX_CACHE_SIZE."""
        import importlib

        # importlib.reload is needed because MAX_CACHE_SIZE is computed once at
        # import time from os.environ. Reload re-executes the module-level code
        # with the patched env var. This is safe here because MAX_CACHE_SIZE is
        # only referenced within this module (by get_client); no other module
        # imports it directly, so stale references aren't a concern.
        monkeypatch.setenv("ANTHROPIC_CLIENT_CACHE_SIZE", "64")
        importlib.reload(anthropic_client_cache)
        try:
            assert anthropic_client_cache.MAX_CACHE_SIZE == 64
        finally:
            monkeypatch.delenv("ANTHROPIC_CLIENT_CACHE_SIZE", raising=False)
            importlib.reload(anthropic_client_cache)

    def test_non_integer_env_var_falls_back_to_default(self, monkeypatch):
        """Non-integer values fall back to the default with a warning."""
        import importlib

        monkeypatch.setenv("ANTHROPIC_CLIENT_CACHE_SIZE", "abc")
        importlib.reload(anthropic_client_cache)
        try:
            assert anthropic_client_cache.MAX_CACHE_SIZE == anthropic_client_cache._DEFAULT_MAX_CACHE_SIZE
        finally:
            monkeypatch.delenv("ANTHROPIC_CLIENT_CACHE_SIZE", raising=False)
            importlib.reload(anthropic_client_cache)

    def test_zero_env_var_clamped_to_one(self, monkeypatch):
        """Zero is clamped to 1 so the cache always holds at least one client."""
        import importlib

        monkeypatch.setenv("ANTHROPIC_CLIENT_CACHE_SIZE", "0")
        importlib.reload(anthropic_client_cache)
        try:
            assert anthropic_client_cache.MAX_CACHE_SIZE == 1
        finally:
            monkeypatch.delenv("ANTHROPIC_CLIENT_CACHE_SIZE", raising=False)
            importlib.reload(anthropic_client_cache)

    def test_negative_env_var_clamped_to_one(self, monkeypatch):
        """Negative values are clamped to 1."""
        import importlib

        monkeypatch.setenv("ANTHROPIC_CLIENT_CACHE_SIZE", "-5")
        importlib.reload(anthropic_client_cache)
        try:
            assert anthropic_client_cache.MAX_CACHE_SIZE == 1
        finally:
            monkeypatch.delenv("ANTHROPIC_CLIENT_CACHE_SIZE", raising=False)
            importlib.reload(anthropic_client_cache)
