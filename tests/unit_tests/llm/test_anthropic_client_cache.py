"""Tests for AnthropicClient LRU cache.

Verifies that passthrough requests reuse cached client instances
instead of creating a new AnthropicClient (and underlying TCP connection)
per request.
"""

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

    def test_returns_anthropic_client(self):
        client = anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key")
        assert isinstance(client, AnthropicClient)

    def test_same_credential_returns_same_instance(self):
        c1 = anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key")
        c2 = anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key")
        assert c1 is c2

    def test_different_credentials_return_different_instances(self):
        c1 = anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key")
        c2 = anthropic_client_cache.get_client("sk-ant-api03-key2", auth_type="api_key")
        assert c1 is not c2

    def test_different_auth_types_return_different_instances(self):
        """api_key and auth_token for the same credential are separate cache entries."""
        c1 = anthropic_client_cache.get_client("token-abc", auth_type="api_key")
        c2 = anthropic_client_cache.get_client("token-abc", auth_type="auth_token")
        assert c1 is not c2

    def test_different_base_urls_return_different_instances(self):
        c1 = anthropic_client_cache.get_client("sk-ant-api03-key1", auth_type="api_key", base_url=None)
        c2 = anthropic_client_cache.get_client(
            "sk-ant-api03-key1", auth_type="api_key", base_url="https://custom.api.com"
        )
        assert c1 is not c2

    def test_same_base_url_returns_same_instance(self):
        c1 = anthropic_client_cache.get_client(
            "sk-ant-api03-key1", auth_type="api_key", base_url="https://custom.api.com"
        )
        c2 = anthropic_client_cache.get_client(
            "sk-ant-api03-key1", auth_type="api_key", base_url="https://custom.api.com"
        )
        assert c1 is c2

    def test_api_key_auth_type_passes_api_key_kwarg(self):
        """Clients created with auth_type='api_key' use the api_key constructor param."""
        with patch.object(anthropic_client_cache, "AnthropicClient") as mock_cls:
            mock_cls.return_value = AnthropicClient(api_key="dummy")
            anthropic_client_cache.get_client("the-key", auth_type="api_key", base_url="https://x.com")
            mock_cls.assert_called_once_with(api_key="the-key", base_url="https://x.com")

    def test_auth_token_auth_type_passes_auth_token_kwarg(self):
        """Clients created with auth_type='auth_token' use the auth_token constructor param."""
        with patch.object(anthropic_client_cache, "AnthropicClient") as mock_cls:
            mock_cls.return_value = AnthropicClient(api_key="dummy")
            anthropic_client_cache.get_client("oauth-tok", auth_type="auth_token", base_url=None)
            mock_cls.assert_called_once_with(auth_token="oauth-tok", base_url=None)


class TestLRUEviction:
    """Cache respects max size and evicts least-recently-used entries."""

    def test_evicts_oldest_when_full(self):
        max_size = anthropic_client_cache.MAX_CACHE_SIZE
        # Fill cache to capacity
        clients = []
        for i in range(max_size):
            c = anthropic_client_cache.get_client(f"key-{i}", auth_type="api_key")
            clients.append(c)
        assert anthropic_client_cache.cache_size() == max_size

        # One more triggers eviction of key-0
        new_client = anthropic_client_cache.get_client("key-overflow", auth_type="api_key")
        assert anthropic_client_cache.cache_size() == max_size  # still at max
        assert new_client is not clients[0]

        # key-0 was evicted — requesting it creates a new instance
        refetched = anthropic_client_cache.get_client("key-0", auth_type="api_key")
        assert refetched is not clients[0]

    def test_cache_hit_refreshes_lru_position(self):
        max_size = anthropic_client_cache.MAX_CACHE_SIZE
        # Fill cache
        for i in range(max_size):
            anthropic_client_cache.get_client(f"key-{i}", auth_type="api_key")

        # Access key-0 to refresh it (move to most-recently-used)
        refreshed = anthropic_client_cache.get_client("key-0", auth_type="api_key")

        # Add one more — should evict key-1 (now the oldest), not key-0
        anthropic_client_cache.get_client("key-overflow", auth_type="api_key")

        # key-0 should still be cached (same instance)
        assert anthropic_client_cache.get_client("key-0", auth_type="api_key") is refreshed

        # key-1 was evicted — requesting it creates a fresh instance
        assert anthropic_client_cache.cache_size() == max_size


class TestCacheManagement:
    """clear() and cache_size() helpers."""

    def test_clear_empties_cache(self):
        anthropic_client_cache.get_client("key-1", auth_type="api_key")
        anthropic_client_cache.get_client("key-2", auth_type="api_key")
        assert anthropic_client_cache.cache_size() == 2

        count = anthropic_client_cache.clear()
        assert count == 2
        assert anthropic_client_cache.cache_size() == 0

    def test_clear_on_empty_returns_zero(self):
        assert anthropic_client_cache.clear() == 0

    def test_cache_size_tracks_entries(self):
        assert anthropic_client_cache.cache_size() == 0
        anthropic_client_cache.get_client("key-1", auth_type="api_key")
        assert anthropic_client_cache.cache_size() == 1
        anthropic_client_cache.get_client("key-2", auth_type="auth_token")
        assert anthropic_client_cache.cache_size() == 2

    def test_cache_size_does_not_count_hits(self):
        """Accessing the same key multiple times doesn't increase size."""
        anthropic_client_cache.get_client("key-1", auth_type="api_key")
        anthropic_client_cache.get_client("key-1", auth_type="api_key")
        anthropic_client_cache.get_client("key-1", auth_type="api_key")
        assert anthropic_client_cache.cache_size() == 1
