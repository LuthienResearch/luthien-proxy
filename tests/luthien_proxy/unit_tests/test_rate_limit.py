from __future__ import annotations

from unittest.mock import patch

import pytest

from luthien_proxy.rate_limit import TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_disabled_never_raises():
    limiter = TokenBucketRateLimiter(rpm=0, burst=0)
    for _ in range(100):
        await limiter.check("key")


@pytest.mark.asyncio
async def test_first_request_allowed():
    limiter = TokenBucketRateLimiter(rpm=60, burst=5)
    await limiter.check("key")


@pytest.mark.asyncio
async def test_requests_within_burst_allowed():
    limiter = TokenBucketRateLimiter(rpm=60, burst=5)
    for _ in range(5):
        await limiter.check("key")


@pytest.mark.asyncio
async def test_exceeding_rpm_raises_429():
    from fastapi import HTTPException

    limiter = TokenBucketRateLimiter(rpm=60, burst=3)
    for _ in range(3):
        await limiter.check("key")
    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("key")
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_429_has_correct_headers():
    from fastapi import HTTPException

    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    await limiter.check("key")
    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("key")
    exc = exc_info.value
    assert exc.status_code == 429
    assert exc.headers is not None
    assert "Retry-After" in exc.headers
    assert exc.headers["X-RateLimit-Limit"] == "60"
    assert exc.headers["X-RateLimit-Remaining"] == "0"
    assert "X-RateLimit-Reset" in exc.headers
    retry_after = int(exc.headers["Retry-After"])
    assert retry_after >= 1


@pytest.mark.asyncio
async def test_different_keys_independent_buckets():
    from fastapi import HTTPException

    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    await limiter.check("key_a")
    await limiter.check("key_b")
    with pytest.raises(HTTPException):
        await limiter.check("key_a")
    with pytest.raises(HTTPException):
        await limiter.check("key_b")
    await limiter.check("key_c")


@pytest.mark.asyncio
async def test_tokens_refill_over_time():
    limiter = TokenBucketRateLimiter(rpm=60, burst=1)

    t0 = 1000.0
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0):
        await limiter.check("key")

    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0 + 1.5):
        await limiter.check("key")


@pytest.mark.asyncio
async def test_burst_defaults_to_rpm_when_zero():
    limiter = TokenBucketRateLimiter(rpm=10, burst=0)
    assert limiter.burst == 10


@pytest.mark.asyncio
async def test_burst_custom_value():
    limiter = TokenBucketRateLimiter(rpm=10, burst=20)
    assert limiter.burst == 20
    for _ in range(20):
        await limiter.check("key")
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await limiter.check("key")
