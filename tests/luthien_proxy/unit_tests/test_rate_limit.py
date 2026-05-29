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
    decision = await limiter.check("key")
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_requests_within_burst_allowed():
    limiter = TokenBucketRateLimiter(rpm=60, burst=5)
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        for _ in range(5):
            decision = await limiter.check("key")
            assert decision.allowed is True


@pytest.mark.asyncio
async def test_exceeding_rpm_denied():
    limiter = TokenBucketRateLimiter(rpm=60, burst=3)
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        for _ in range(3):
            await limiter.check("key")
        decision = await limiter.check("key")
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_denied_decision_fields():
    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    await limiter.check("key")
    decision = await limiter.check("key")
    assert decision.allowed is False
    assert decision.limit == 60
    assert decision.remaining == 0
    assert decision.retry_after >= 1


@pytest.mark.asyncio
async def test_different_keys_independent_buckets():
    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    decision_a1 = await limiter.check("key_a")
    decision_b1 = await limiter.check("key_b")
    assert decision_a1.allowed is True
    assert decision_b1.allowed is True
    decision_a2 = await limiter.check("key_a")
    decision_b2 = await limiter.check("key_b")
    assert decision_a2.allowed is False
    assert decision_b2.allowed is False
    decision_c = await limiter.check("key_c")
    assert decision_c.allowed is True


@pytest.mark.asyncio
async def test_tokens_refill_over_time():
    limiter = TokenBucketRateLimiter(rpm=60, burst=1)

    t0 = 1000.0
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0):
        await limiter.check("key")

    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0 + 1.5):
        decision = await limiter.check("key")
        assert decision.allowed is True


@pytest.mark.asyncio
async def test_burst_defaults_to_rpm_when_zero():
    limiter = TokenBucketRateLimiter(rpm=10, burst=0)
    assert limiter.burst == 10


@pytest.mark.asyncio
async def test_burst_custom_value():
    limiter = TokenBucketRateLimiter(rpm=10, burst=20)
    assert limiter.burst == 20
    for _ in range(20):
        decision = await limiter.check("key")
        assert decision.allowed is True
    decision = await limiter.check("key")
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_concurrent_same_key():
    import asyncio as asyncio_mod

    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        limiter = TokenBucketRateLimiter(rpm=60, burst=60)
        decisions = await asyncio_mod.gather(*[limiter.check("key") for _ in range(60)])
        for decision in decisions:
            assert decision.allowed is True
        decision = await limiter.check("key")
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_retry_after_math():
    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    await limiter.check("key")
    decision = await limiter.check("key")
    assert decision.allowed is False
    assert decision.retry_after == 1


@pytest.mark.asyncio
async def test_reset_timestamp_is_unix():
    import time as time_module

    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    await limiter.check("key")
    decision = await limiter.check("key")
    assert decision.allowed is False
    reset = decision.reset_unix
    now = int(time_module.time())
    assert now <= reset <= now + 5


@pytest.mark.asyncio
async def test_refill_math(monkeypatch):
    import time as time_module

    limiter = TokenBucketRateLimiter(rpm=60, burst=10)
    for _ in range(10):
        await limiter.check("key")
    original_monotonic = time_module.monotonic
    monkeypatch.setattr(
        "luthien_proxy.rate_limit.time.monotonic",
        lambda: original_monotonic() + 5.0,
    )
    for _ in range(5):
        decision = await limiter.check("key")
        assert decision.allowed is True
    decision = await limiter.check("key")
    assert decision.allowed is False


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
async def test_refill_capped_at_burst():
    limiter = TokenBucketRateLimiter(rpm=60, burst=5)
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        for _ in range(5):
            await limiter.check("key")

    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0 + 3600.0):
        for _ in range(5):
            decision = await limiter.check("key")
            assert decision.allowed is True
        decision = await limiter.check("key")
        assert decision.allowed is False


@pytest.mark.asyncio
async def test_steady_state_rate():
    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    t0 = 1000.0
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0):
        await limiter.check("key")

    for elapsed, expected_admitted in [(0.9, 0), (1.0, 1), (1.5, 1), (2.0, 1)]:
        limiter2 = TokenBucketRateLimiter(rpm=60, burst=1)
        with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0):
            await limiter2.check("key")
        with patch("luthien_proxy.rate_limit.time.monotonic", return_value=t0 + elapsed):
            decision = await limiter2.check("key")
            if expected_admitted:
                assert decision.allowed is True
            else:
                assert decision.allowed is False


@pytest.mark.asyncio
async def test_concurrent_different_keys():
    import asyncio as asyncio_mod

    limiter = TokenBucketRateLimiter(rpm=60, burst=1)
    with patch("luthien_proxy.rate_limit.time.monotonic", return_value=1000.0):
        await asyncio_mod.gather(
            limiter.check("key_a"),
            limiter.check("key_b"),
            limiter.check("key_c"),
        )
    assert len(limiter._buckets) == 3
