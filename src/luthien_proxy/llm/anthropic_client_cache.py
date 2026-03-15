"""LRU cache for AnthropicClient instances keyed by credential hash.

Passthrough auth creates a per-credential AnthropicClient. Without caching,
every request spins up a new anthropic.AsyncAnthropic (and its underlying
httpx.AsyncClient connection pool), adding 50-100ms TCP+TLS overhead.

This module maintains a bounded LRU cache so repeated requests with the
same credential reuse the existing client and its warm connection pool.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Literal

from luthien_proxy.credential_manager import hash_credential
from luthien_proxy.llm.anthropic_client import AnthropicClient

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 64

_cache: OrderedDict[str, AnthropicClient] = OrderedDict()
_lock = asyncio.Lock()


def _make_key(credential_hash: str, auth_type: str, base_url: str | None) -> str:
    return f"{credential_hash}:{auth_type}:{base_url or ''}"


async def get_client(
    credential: str,
    *,
    auth_type: Literal["api_key", "auth_token"],
    base_url: str | None = None,
) -> AnthropicClient:
    """Return a cached AnthropicClient, creating one on cache miss.

    Cache key is derived from SHA-256(credential) + auth_type + base_url,
    so raw credentials are never stored as keys.
    """
    key = _make_key(hash_credential(credential), auth_type, base_url)

    async with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]

        if auth_type == "api_key":
            client = AnthropicClient(api_key=credential, base_url=base_url)
        else:
            client = AnthropicClient(auth_token=credential, base_url=base_url)

        if len(_cache) >= MAX_CACHE_SIZE:
            evicted_key, evicted_client = _cache.popitem(last=False)
            asyncio.create_task(evicted_client.close())
            logger.debug(f"Evicted AnthropicClient from cache: {evicted_key[:32]}...")

        _cache[key] = client
        logger.debug(f"Cached new AnthropicClient (cache_size={len(_cache)})")
        return client


def clear() -> int:
    """Remove all cached clients. Returns count cleared."""
    count = len(_cache)
    _cache.clear()
    return count


def cache_size() -> int:
    """Current number of cached clients."""
    return len(_cache)
