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
    from unittest.mock import patch

    limiter = TokenBucketRateLimiter(rpm=60, burst=5)
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        for _ in range(5):
            await limiter.check("key")


@pytest.mark.asyncio
async def test_exceeding_rpm_raises_429():
    from unittest.mock import patch

    from fastapi import HTTPException

    limiter = TokenBucketRateLimiter(rpm=60, burst=3)
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
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


@pytest.mark.asyncio
async def test_concurrent_same_key():
    import asyncio as asyncio_mod
    from unittest.mock import patch

    from fastapi import HTTPException

    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        limiter = TokenBucketRateLimiter(rpm=60, burst=60)
        await asyncio_mod.gather(*[limiter.check("key") for _ in range(60)])
        with pytest.raises(HTTPException) as exc_info:
            await limiter.check("key")
    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_retry_after_math():
    from fastapi import HTTPException

    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    await limiter.check("key")
    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("key")
    headers = exc_info.value.headers
    assert headers is not None
    assert int(headers["Retry-After"]) == 1


@pytest.mark.asyncio
async def test_reset_timestamp_is_unix():
    import time as time_module

    from fastapi import HTTPException

    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    await limiter.check("key")
    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("key")
    headers = exc_info.value.headers
    assert headers is not None
    reset = int(headers["X-RateLimit-Reset"])
    now = int(time_module.time())
    assert now <= reset <= now + 5


@pytest.mark.asyncio
async def test_refill_math(monkeypatch):
    import time as time_module

    from fastapi import HTTPException

    limiter = TokenBucketRateLimiter(rpm=60, burst=10)
    for _ in range(10):
        await limiter.check("key")
    original_monotonic = time_module.monotonic
    monkeypatch.setattr("luthien_proxy.rate_limit.time.monotonic", lambda: original_monotonic() + 5.0)
    for _ in range(5):
        await limiter.check("key")
    with pytest.raises(HTTPException):
        await limiter.check("key")


@pytest.mark.asyncio
async def test_lru_eviction_fifo_when_no_access():
    limiter = TokenBucketRateLimiter(rpm=60, burst=60, max_keys=3)
    await limiter.check("key1")
    await limiter.check("key2")
    await limiter.check("key3")
    assert len(limiter._buckets) == 3
    key1_hash = limiter._hash_key("key1")
    await limiter.check("key4")
    assert len(limiter._buckets) == 3
    assert key1_hash not in limiter._buckets


@pytest.mark.asyncio
async def test_lru_eviction_spares_recently_accessed():
    limiter = TokenBucketRateLimiter(rpm=60, burst=60, max_keys=3)
    await limiter.check("key1")
    await limiter.check("key2")
    await limiter.check("key3")
    await limiter.check("key1")
    key2_hash = limiter._hash_key("key2")
    await limiter.check("key4")
    assert len(limiter._buckets) == 3
    assert key2_hash not in limiter._buckets


@pytest.mark.asyncio
async def test_key_is_hashed():
    import hashlib

    limiter = TokenBucketRateLimiter(rpm=60, burst=60)
    raw_key = "sk-secret-token-12345"
    await limiter.check(raw_key)
    assert raw_key not in limiter._buckets
    stored_key = next(iter(limiter._buckets))
    assert stored_key == hashlib.sha256(raw_key.encode()).hexdigest()


def test_negative_rpm_raises():
    with pytest.raises(ValueError, match="rpm"):
        TokenBucketRateLimiter(rpm=-1, burst=0)


def test_negative_burst_raises():
    with pytest.raises(ValueError, match="burst"):
        TokenBucketRateLimiter(rpm=60, burst=-1)


def test_zero_max_keys_raises():
    with pytest.raises(ValueError, match="max_keys"):
        TokenBucketRateLimiter(rpm=60, burst=0, max_keys=0)


@pytest.mark.asyncio
async def test_client_key_mode_shared_bucket():
    from unittest.mock import patch

    from fastapi import HTTPException

    limiter = TokenBucketRateLimiter(rpm=60, burst=60)
    shared_key = "shared-client-api-key"
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        for _ in range(60):
            await limiter.check(shared_key)
        with pytest.raises(HTTPException) as exc_info:
            await limiter.check(shared_key)
    assert exc_info.value.status_code == 429
    assert len(limiter._buckets) == 1


@pytest.mark.asyncio
async def test_refill_capped_at_burst():
    from unittest.mock import patch

    limiter = TokenBucketRateLimiter(rpm=5, burst=5)
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        for _ in range(5):
            await limiter.check("key")

    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0 + 3600.0):
        for _ in range(5):
            await limiter.check("key")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await limiter.check("key")
        assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_steady_state_rate():
    from unittest.mock import patch

    from fastapi import HTTPException

    limiter = TokenBucketRateLimiter(rpm=60, burst=60)
    t0 = 1000.0
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0):
        for _ in range(60):
            await limiter.check("key")

    for elapsed, expected_admitted in [(0.9, 0), (1.0, 1), (1.5, 1), (2.0, 1)]:
        limiter2 = TokenBucketRateLimiter(rpm=60, burst=60)
        with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0):
            for _ in range(60):
                await limiter2.check("key")
        with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0 + elapsed):
            if expected_admitted:
                await limiter2.check("key")
            else:
                with pytest.raises(HTTPException):
                    await limiter2.check("key")


@pytest.mark.asyncio
async def test_concurrent_different_keys():
    import asyncio as asyncio_mod
    from unittest.mock import patch

    limiter = TokenBucketRateLimiter(rpm=60, burst=60)
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        await asyncio_mod.gather(
            limiter.check("key_a"),
            limiter.check("key_b"),
            limiter.check("key_c"),
        )
    assert len(limiter._buckets) == 3
