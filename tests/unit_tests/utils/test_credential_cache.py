"""Unit tests for in-process credential cache."""

import asyncio
import time

import pytest

from luthien_proxy.utils.credential_cache import InProcessCredentialCache


class TestInProcessCredentialCache:
    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing_key(self):
        cache = InProcessCredentialCache()
        assert await cache.get("missing") is None

    @pytest.mark.asyncio
    async def test_setex_and_get(self):
        cache = InProcessCredentialCache()
        await cache.setex("key1", 60, "value1")
        assert await cache.get("key1") == "value1"

    @pytest.mark.asyncio
    async def test_expired_key_returns_none(self):
        cache = InProcessCredentialCache()
        await cache.setex("key1", 0, "value1")  # expires immediately
        await asyncio.sleep(0.01)
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_delete_removes_key(self):
        cache = InProcessCredentialCache()
        await cache.setex("key1", 60, "value1")
        result = await cache.delete("key1")
        assert result is True
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_delete_missing_key_returns_false(self):
        cache = InProcessCredentialCache()
        result = await cache.delete("missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_ttl_returns_remaining_seconds(self):
        cache = InProcessCredentialCache()
        await cache.setex("key1", 60, "value1")
        remaining = await cache.ttl("key1")
        assert 58 <= remaining <= 60

    @pytest.mark.asyncio
    async def test_ttl_missing_key_returns_negative(self):
        cache = InProcessCredentialCache()
        assert await cache.ttl("missing") == -2

    @pytest.mark.asyncio
    async def test_scan_iter_yields_matching_keys(self):
        cache = InProcessCredentialCache()
        await cache.setex("prefix:a", 60, "va")
        await cache.setex("prefix:b", 60, "vb")
        await cache.setex("other:c", 60, "vc")

        keys = [k async for k in cache.scan_iter(match="prefix:*")]
        assert set(keys) == {"prefix:a", "prefix:b"}

    @pytest.mark.asyncio
    async def test_scan_iter_skips_expired(self):
        cache = InProcessCredentialCache()
        await cache.setex("prefix:a", 0, "va")  # expired
        await cache.setex("prefix:b", 60, "vb")
        await asyncio.sleep(0.01)

        keys = [k async for k in cache.scan_iter(match="prefix:*")]
        assert keys == ["prefix:b"]

    @pytest.mark.asyncio
    async def test_unlink_bulk_deletes(self):
        cache = InProcessCredentialCache()
        await cache.setex("a", 60, "va")
        await cache.setex("b", 60, "vb")
        await cache.setex("c", 60, "vc")

        count = await cache.unlink("a", "b")
        assert count == 2
        assert await cache.get("a") is None
        assert await cache.get("c") == "vc"
