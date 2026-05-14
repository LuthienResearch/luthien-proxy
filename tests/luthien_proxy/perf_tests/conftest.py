"""Shared fixtures and helpers for performance tests.

This module provides infrastructure for perf tests including:
- Isolated perf test gateway (separate from dev DB)
- Browser automation via Playwright
- Timing measurement utilities
- Sami-like fixture data loading
"""

import pytest


@pytest.fixture
def perf_db_path():
    """Path to isolated SQLite database for perf tests.

    Fixture implementation: P9 will create a temporary SQLite DB
    separate from ~/.luthien/local.db to avoid contaminating dev data.
    """
    pass


@pytest.fixture
async def perf_gateway_url():
    """URL of the perf test gateway.

    Fixture implementation: P9 will spin up an in-process FastAPI gateway
    with the isolated perf_db_path, returning the base URL (e.g., http://localhost:9999).
    """
    pass


@pytest.fixture
async def perf_admin_api_key():
    """Admin API key for the perf test gateway.

    Fixture implementation: P9 will generate a test admin key for policy management.
    """
    pass


@pytest.fixture
async def browser():
    """Chromium browser instance for perf tests.

    Fixture implementation: P9 will launch Playwright Chromium with CDP enabled
    for bandwidth shaping and performance measurement.
    """
    pass


@pytest.fixture
async def page(browser):
    """Browser page context for perf tests.

    Fixture implementation: P9 will create a new page within the browser context,
    with performance observer and timing hooks installed.
    """
    pass


@pytest.fixture
def measure_time():
    """Context manager for latency measurement.

    Fixture implementation: P9 will provide a context manager that:
    - Records wall-clock time on entry
    - Returns elapsed milliseconds on exit
    - Supports nested measurements

    Usage:
        with measure_time() as timer:
            # code to measure
        elapsed_ms = timer.elapsed
    """
    pass


@pytest.fixture
async def sami_fixture_data():
    """Sami-like fixture data: 78 sessions, largest ~442 messages.

    Fixture implementation: P9 will load or generate fixture data matching
    Sami's deployment shape (78 sessions, one 442-message outlier, rest small).
    """
    pass
