"""Shared fixtures and helpers for performance tests.

Infrastructure for perf tests: isolated gateway (perf DB, never dev DB),
Playwright browser automation, Navigation Timing capture, and n_runs statistics
that separate the cold-cache first run from warm runs.
"""

from __future__ import annotations

import asyncio
import os
import socket
import statistics
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any, Callable

import pytest
import uvicorn
from playwright.async_api import Browser, Page, async_playwright

from luthien_proxy.main import create_app
from luthien_proxy.perf.db import get_perf_db_url, migrate_perf_db
from luthien_proxy.settings import clear_settings_cache
from luthien_proxy.utils.db import DatabasePool


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add --update-snapshots option to pytest."""
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="Regenerate snapshot files",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.pluginmanager.has_plugin("xdist"):
        raise pytest.UsageError(
            "Perf tests cannot run under pytest-xdist: the session-scoped "
            "perf_gateway_url fixture mutates os.environ, which would race "
            "across workers. Run perf tests with: ./scripts/run_perf.sh"
        )


_ADMIN_KEY = "admin-dev-key"
_API_KEY = "sk-perf-test-key"


@dataclass
class PageLoadMetrics:
    ttfb_ms: float
    dcl_ms: float
    load_ms: float
    ttfm_ms: float  # time-to-first-mutation on #main (0 if no mutation observed)


@dataclass
class ScrollFPSMetrics:
    p50_frame_ms: float
    p95_frame_ms: float
    p99_frame_ms: float
    n_frames: int


@dataclass
class RunStats:
    cold_ms: float  # first run — cold cache, excluded from warm stats
    warm_median_ms: float
    warm_p95_ms: float
    n_warm: int


def _percentile(sorted_data: list[float], pct: float) -> float:
    if not sorted_data:
        return 0.0
    idx = min(int(len(sorted_data) * pct), len(sorted_data) - 1)
    return sorted_data[idx]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def n_runs(fn: Callable[[], Any], n: int = 5) -> RunStats:
    """Run fn N times; first run is cold-cache and excluded from median/p95.

    fn may be async or sync and must return a float (elapsed ms).
    """
    times: list[float] = []
    for _ in range(n):
        result = fn()
        if asyncio.iscoroutine(result):
            result = await result
        times.append(float(result))

    cold = times[0]
    warm = times[1:]
    sorted_warm = sorted(warm)

    return RunStats(
        cold_ms=cold,
        warm_median_ms=statistics.median(warm) if warm else 0.0,
        warm_p95_ms=_percentile(sorted_warm, 0.95),
        n_warm=len(warm),
    )


async def measure_page_load(page: Page, url: str) -> PageLoadMetrics:
    """Navigate to url and return Navigation Timing + first-mutation metrics.

    Uses add_init_script so the MutationObserver is installed before any page
    JS runs — necessary because the mutation may fire during initial render.
    """
    # Guard flag prevents duplicate observers when called multiple times on the same page.
    await page.add_init_script("""
        if (!window.__perfObserverInstalled) {
            window.__perfObserverInstalled = true;
            window.__firstMutation = null;
            function _setupMutObs() {
                var target = document.getElementById('main') || document.body;
                var obs = new MutationObserver(function() {
                    if (window.__firstMutation === null) {
                        window.__firstMutation = performance.now();
                        obs.disconnect();
                    }
                });
                obs.observe(target, { childList: true, subtree: true });
            }
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', _setupMutObs);
            } else {
                _setupMutObs();
            }
        }
    """)

    await page.goto(url, wait_until="networkidle")

    metrics: dict[str, float] = await page.evaluate("""() => {
        var entries = window.performance.getEntriesByType('navigation');
        if (entries.length > 0) {
            var nav = entries[0];
            return {
                ttfb: nav.responseStart,
                dcl: nav.domContentLoadedEventEnd,
                load: nav.loadEventEnd,
                ttfm: window.__firstMutation || 0
            };
        }
        var t = window.performance.timing;
        var origin = t.fetchStart;
        return {
            ttfb: t.responseStart - origin,
            dcl: t.domContentLoadedEventEnd - origin,
            load: t.loadEventEnd - origin,
            ttfm: window.__firstMutation || 0
        };
    }""")

    return PageLoadMetrics(
        ttfb_ms=metrics["ttfb"],
        dcl_ms=metrics["dcl"],
        load_ms=metrics["load"],
        ttfm_ms=metrics["ttfm"],
    )


async def measure_scroll_fps(page: Page, selector: str) -> ScrollFPSMetrics:
    """Scroll selector for 5 s via rAF and return p50/p95/p99 frame times.

    Returns a Promise from page.evaluate so Playwright waits for the full
    5-second measurement without blocking the Python event loop.
    """
    page.set_default_timeout(10_000)

    frame_times: list[float] = await page.evaluate(
        """(selector) => {
            return new Promise(function(resolve) {
                var el = document.querySelector(selector) || document.body;
                var frameTimes = [];
                var lastTime = performance.now();
                var rafId = null;
                var done = false;

                function tick(now) {
                    if (done) { return; }
                    var delta = now - lastTime;
                    if (delta > 0) { frameTimes.push(delta); }
                    lastTime = now;
                    el.scrollTop += 80;
                    if (el.scrollTop + el.clientHeight >= el.scrollHeight) {
                        el.scrollTop = 0;
                    }
                    rafId = requestAnimationFrame(tick);
                }

                setTimeout(function() {
                    done = true;
                    if (rafId !== null) { cancelAnimationFrame(rafId); }
                    resolve(frameTimes);
                }, 5000);

                rafId = requestAnimationFrame(tick);
            });
        }""",
        selector,
    )

    if not frame_times:
        return ScrollFPSMetrics(p50_frame_ms=0.0, p95_frame_ms=0.0, p99_frame_ms=0.0, n_frames=0)

    sorted_times = sorted(frame_times)
    return ScrollFPSMetrics(
        p50_frame_ms=_percentile(sorted_times, 0.50),
        p95_frame_ms=_percentile(sorted_times, 0.95),
        p99_frame_ms=_percentile(sorted_times, 0.99),
        n_frames=len(sorted_times),
    )


@pytest.fixture(scope="session")
def perf_db_url() -> str:
    url = get_perf_db_url("sqlite")
    migrate_perf_db("sqlite")
    return url


@pytest.fixture(scope="session")
def perf_gateway_url(perf_db_url: str) -> Iterator[str]:
    """In-process FastAPI gateway on a random port backed by the perf DB.

    ANTHROPIC_BASE_URL is pointed at 127.0.0.1:1 (unreachable) so any
    accidental upstream call fails immediately rather than hanging.
    """
    port = _free_port()
    db_pool = DatabasePool(perf_db_url)

    # NOTE: perf tests must run in a dedicated pytest session (run_perf.sh enforces this).
    # Under pytest-xdist or parallel sessions this os.environ mutation would race.
    saved_env: dict[str, str | None] = {k: os.environ.get(k) for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY")}

    def restore_env() -> None:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:1"
    os.environ["ANTHROPIC_API_KEY"] = "mock-key"
    clear_settings_cache()

    app = create_app(
        api_key=_API_KEY,
        admin_key=_ADMIN_KEY,
        db_pool=db_pool,
        redis_client=None,
        startup_policy_path="config/policy_config.yaml",
        policy_source="db-fallback-file",
    )

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="perf-gateway")
    thread.start()

    deadline = time.monotonic() + 10
    started = False
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                started = True
                break
        except OSError:
            time.sleep(0.1)

    if not started:
        server.should_exit = True
        thread.join(timeout=5)
        restore_env()
        clear_settings_cache()
        asyncio.run(db_pool.close())
        raise RuntimeError("Perf gateway did not start within 10 s")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)
    restore_env()
    clear_settings_cache()
    asyncio.run(db_pool.close())


@pytest.fixture(scope="session")
def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_ADMIN_KEY}"}


@pytest.fixture(scope="session")
async def playwright_browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--disable-cache", "--disable-gpu"])
        yield browser
        await browser.close()


@pytest.fixture
async def playwright_page(playwright_browser: Browser) -> AsyncIterator[Page]:
    """Fresh browser context per test — no cookie/cache bleed across tests."""
    context = await playwright_browser.new_context()
    page = await context.new_page()
    yield page
    await context.close()
