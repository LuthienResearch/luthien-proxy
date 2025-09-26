import asyncio

import pytest

from luthien_proxy.control_plane.utils.rate_limiter import RateLimitExceeded, RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_threshold():
    limiter = RateLimiter(max_events=2, window_seconds=0.05)
    assert await limiter.try_acquire("client") is True
    assert await limiter.try_acquire("client") is True
    assert await limiter.try_acquire("client") is False
    await asyncio.sleep(0.06)
    assert await limiter.try_acquire("client") is True


@pytest.mark.asyncio
async def test_acquire_or_raise_raises_when_exceeded():
    limiter = RateLimiter(max_events=1, window_seconds=0.1)
    await limiter.acquire_or_raise("client")
    with pytest.raises(RateLimitExceeded):
        await limiter.acquire_or_raise("client")
    await asyncio.sleep(0.11)
    await limiter.acquire_or_raise("client")
