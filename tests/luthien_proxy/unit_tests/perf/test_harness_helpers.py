"""Unit tests for perf test harness helpers: n_runs, RunStats, PageLoadMetrics."""

from __future__ import annotations

from tests.luthien_proxy.perf_tests.conftest import (
    PageLoadMetrics,
    _percentile,
    n_runs,
)


def test_page_load_metrics_dataclass():
    m = PageLoadMetrics(ttfb_ms=10.0, dcl_ms=50.0, load_ms=120.0, ttfm_ms=200.0)
    assert m.ttfb_ms == 10.0
    assert m.dcl_ms == 50.0
    assert m.load_ms == 120.0
    assert m.ttfm_ms == 200.0


def test_run_stats_median():
    """warm_median_ms is the statistics.median of warm runs, not affected by cold."""
    cold_value = 9999.0
    warm_values = [10.0, 20.0, 30.0, 40.0]
    call_idx = 0
    all_values = [cold_value] + warm_values

    def fn() -> float:
        nonlocal call_idx
        val = all_values[call_idx]
        call_idx += 1
        return val

    import asyncio

    stats = asyncio.run(n_runs(fn, n=5))
    assert stats.cold_ms == cold_value
    assert stats.warm_median_ms == 25.0  # median([10, 20, 30, 40]) = 25.0
    assert stats.n_warm == 4


async def test_n_runs_separates_cold_cache():
    """Cold-cache first run must NOT be included in warm_median_ms."""
    cold_value = 1000.0
    warm_value = 10.0
    call_idx = 0

    def fn() -> float:
        nonlocal call_idx
        call_idx += 1
        return cold_value if call_idx == 1 else warm_value

    stats = await n_runs(fn, n=5)

    assert stats.cold_ms == cold_value
    assert stats.warm_median_ms == warm_value
    assert stats.warm_median_ms != cold_value
    assert stats.n_warm == 4


async def test_n_runs_async_fn():
    """n_runs works with async callables too."""
    call_idx = 0

    async def async_fn() -> float:
        nonlocal call_idx
        call_idx += 1
        return float(call_idx * 10)

    stats = await n_runs(async_fn, n=4)
    assert stats.cold_ms == 10.0
    assert stats.n_warm == 3
    assert stats.warm_median_ms == 30.0  # median([20, 30, 40]) = 30.0


async def test_n_runs_p95():
    """warm_p95_ms uses 95th percentile of warm runs."""
    values = [100.0, 10.0, 10.0, 10.0, 10.0, 10.0]  # cold=100, warm=[10, 10, 10, 10, 10]
    call_idx = 0

    def fn() -> float:
        nonlocal call_idx
        val = values[call_idx]
        call_idx += 1
        return val

    stats = await n_runs(fn, n=6)
    assert stats.cold_ms == 100.0
    assert stats.warm_p95_ms == 10.0
    assert stats.n_warm == 5


def test_percentile_empty():
    assert _percentile([], 0.95) == 0.0


def test_percentile_single():
    assert _percentile([42.0], 0.95) == 42.0


def test_percentile_values():
    data = sorted([10.0, 20.0, 30.0, 40.0, 50.0])
    assert _percentile(data, 0.50) == 30.0
    assert _percentile(data, 0.0) == 10.0
