# Usage Telemetry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add anonymous usage telemetry that periodically sends aggregate metrics (token counts, request counts, active sessions) to a central endpoint for product analytics.

**Architecture:** In-memory counters in the gateway process, rolled up every 5 minutes and POSTed as JSON to a configurable HTTPS endpoint. Opt-out via env var > DB > default-enabled. Collector injected via Dependencies container.

**Tech Stack:** Python 3.13, FastAPI, httpx, asyncpg, pytest

**Design doc:** `docs/plans/2026-03-05-usage-telemetry-design.md`

---

### Task 1: DB Migration — `telemetry_config` table

**Files:**
- Create: `migrations/009_add_telemetry_config.sql`

**Step 1: Write the migration**

```sql
-- ABOUTME: Add telemetry_config table for usage telemetry opt-out and deployment identity
-- ABOUTME: Single-row table storing telemetry enabled state and unique deployment ID

CREATE TABLE IF NOT EXISTS telemetry_config (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled BOOLEAN,  -- null = use default (enabled)
    deployment_id UUID NOT NULL DEFAULT gen_random_uuid(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    updated_by TEXT
);

-- Seed with a single row so deployment_id is generated immediately
INSERT INTO telemetry_config (id) VALUES (1) ON CONFLICT DO NOTHING;
```

**Step 2: Verify migration numbering**

Check `migrations/` directory — the highest existing migration number determines the next one. Currently `008_*` files exist, so `009_` is correct.

**Step 3: Commit**

```bash
git add migrations/009_add_telemetry_config.sql
git commit -m "feat: add telemetry_config migration for usage telemetry"
```

---

### Task 2: Settings — add telemetry env vars

**Files:**
- Modify: `src/luthien_proxy/settings.py:56` (after `enable_request_logging`)
- Modify: `.env.example` (at bottom)

**Step 1: Write the failing test**

Create `tests/unit_tests/test_settings_telemetry.py`:

```python
"""Tests for telemetry-related settings."""

from luthien_proxy.settings import Settings


class TestTelemetrySettings:
    def test_usage_telemetry_defaults_to_none(self):
        """Env var not set means None (defer to DB)."""
        s = Settings(proxy_api_key="k", admin_api_key="k", database_url="postgres://x")
        assert s.usage_telemetry is None

    def test_usage_telemetry_true(self):
        s = Settings(
            proxy_api_key="k", admin_api_key="k", database_url="postgres://x",
            usage_telemetry=True,
        )
        assert s.usage_telemetry is True

    def test_usage_telemetry_false(self):
        s = Settings(
            proxy_api_key="k", admin_api_key="k", database_url="postgres://x",
            usage_telemetry=False,
        )
        assert s.usage_telemetry is False

    def test_telemetry_endpoint_default(self):
        s = Settings(proxy_api_key="k", admin_api_key="k", database_url="postgres://x")
        assert s.telemetry_endpoint == "https://telemetry.luthien.io/v1/events"

    def test_telemetry_endpoint_override(self):
        s = Settings(
            proxy_api_key="k", admin_api_key="k", database_url="postgres://x",
            telemetry_endpoint="https://custom.example.com/v1/events",
        )
        assert s.telemetry_endpoint == "https://custom.example.com/v1/events"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit_tests/test_settings_telemetry.py -v`
Expected: FAIL — `usage_telemetry` and `telemetry_endpoint` not defined on Settings.

**Step 3: Add settings fields**

In `src/luthien_proxy/settings.py`, after the `enable_request_logging` field (line ~56), add:

```python
    # Usage telemetry (anonymous aggregate metrics sent to central endpoint)
    # None = defer to DB config; True/False = env var takes precedence over DB
    usage_telemetry: bool | None = None
    telemetry_endpoint: str = "https://telemetry.luthien.io/v1/events"
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit_tests/test_settings_telemetry.py -v`
Expected: PASS

**Step 5: Update `.env.example`**

Add at the bottom of `.env.example`:

```bash
# Anonymous usage telemetry — aggregate metrics (token counts, request counts)
# sent periodically to help the Luthien team understand product usage.
# No model names, API keys, IPs, or content is ever sent.
# Set to false to disable. Omit to use the value configured in the admin UI.
# USAGE_TELEMETRY=true
# TELEMETRY_ENDPOINT=https://telemetry.luthien.io/v1/events
```

**Step 6: Commit**

```bash
git add src/luthien_proxy/settings.py .env.example tests/unit_tests/test_settings_telemetry.py
git commit -m "feat: add USAGE_TELEMETRY and TELEMETRY_ENDPOINT settings"
```

---

### Task 3: Collector — in-memory counters

**Files:**
- Create: `src/luthien_proxy/usage_telemetry/__init__.py`
- Create: `src/luthien_proxy/usage_telemetry/collector.py`
- Create: `tests/unit_tests/usage_telemetry/__init__.py`
- Create: `tests/unit_tests/usage_telemetry/test_collector.py`

**Step 1: Write the failing tests**

Create `tests/unit_tests/usage_telemetry/test_collector.py`:

```python
"""Tests for usage telemetry collector."""

import pytest

from luthien_proxy.usage_telemetry.collector import UsageCollector


class TestUsageCollector:
    def test_initial_state_is_zero(self):
        c = UsageCollector()
        snapshot = c.snapshot_and_reset()
        assert snapshot["requests_accepted"] == 0
        assert snapshot["requests_completed"] == 0
        assert snapshot["input_tokens"] == 0
        assert snapshot["output_tokens"] == 0
        assert snapshot["streaming_requests"] == 0
        assert snapshot["non_streaming_requests"] == 0
        assert snapshot["sessions_with_ids"] == 0

    def test_record_accepted_increments(self):
        c = UsageCollector()
        c.record_accepted()
        c.record_accepted()
        snapshot = c.snapshot_and_reset()
        assert snapshot["requests_accepted"] == 2

    def test_record_completed_streaming(self):
        c = UsageCollector()
        c.record_completed(is_streaming=True)
        snapshot = c.snapshot_and_reset()
        assert snapshot["requests_completed"] == 1
        assert snapshot["streaming_requests"] == 1
        assert snapshot["non_streaming_requests"] == 0

    def test_record_completed_non_streaming(self):
        c = UsageCollector()
        c.record_completed(is_streaming=False)
        snapshot = c.snapshot_and_reset()
        assert snapshot["requests_completed"] == 1
        assert snapshot["streaming_requests"] == 0
        assert snapshot["non_streaming_requests"] == 1

    def test_record_tokens(self):
        c = UsageCollector()
        c.record_tokens(input_tokens=100, output_tokens=50)
        c.record_tokens(input_tokens=200, output_tokens=75)
        snapshot = c.snapshot_and_reset()
        assert snapshot["input_tokens"] == 300
        assert snapshot["output_tokens"] == 125

    def test_record_session_deduplicates(self):
        c = UsageCollector()
        c.record_session("session-1")
        c.record_session("session-1")
        c.record_session("session-2")
        snapshot = c.snapshot_and_reset()
        assert snapshot["sessions_with_ids"] == 2

    def test_record_session_ignores_none(self):
        c = UsageCollector()
        c.record_session(None)
        snapshot = c.snapshot_and_reset()
        assert snapshot["sessions_with_ids"] == 0

    def test_snapshot_resets_counters(self):
        c = UsageCollector()
        c.record_accepted()
        c.record_tokens(input_tokens=100, output_tokens=50)
        c.record_session("s1")
        first = c.snapshot_and_reset()
        assert first["requests_accepted"] == 1

        second = c.snapshot_and_reset()
        assert second["requests_accepted"] == 0
        assert second["input_tokens"] == 0
        assert second["sessions_with_ids"] == 0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_collector.py -v`
Expected: FAIL — module not found.

**Step 3: Create `__init__.py` files**

Create empty `src/luthien_proxy/usage_telemetry/__init__.py`:

```python
"""Anonymous usage telemetry — aggregate metrics sent to central endpoint."""
```

Create empty `tests/unit_tests/usage_telemetry/__init__.py` (empty file).

**Step 4: Write the collector**

Create `src/luthien_proxy/usage_telemetry/collector.py`:

```python
"""In-memory usage counters with atomic snapshot-and-reset.

Thread safety: all methods use a threading lock because counters may be
incremented from async tasks running on different threads (e.g. streaming
finalizers). The lock is never held for I/O so contention is negligible.
"""

from __future__ import annotations

import threading
from typing import TypedDict


class MetricsSnapshot(TypedDict):
    requests_accepted: int
    requests_completed: int
    input_tokens: int
    output_tokens: int
    streaming_requests: int
    non_streaming_requests: int
    sessions_with_ids: int


class UsageCollector:
    """Collects aggregate usage metrics in memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests_accepted = 0
        self._requests_completed = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._streaming_requests = 0
        self._non_streaming_requests = 0
        self._session_ids: set[str] = set()

    def record_accepted(self) -> None:
        """Record that a request was accepted into the pipeline."""
        with self._lock:
            self._requests_accepted += 1

    def record_completed(self, *, is_streaming: bool) -> None:
        """Record that a request completed successfully."""
        with self._lock:
            self._requests_completed += 1
            if is_streaming:
                self._streaming_requests += 1
            else:
                self._non_streaming_requests += 1

    def record_tokens(self, *, input_tokens: int, output_tokens: int) -> None:
        """Record token usage (Anthropic path only)."""
        with self._lock:
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens

    def record_session(self, session_id: str | None) -> None:
        """Record a session ID if present."""
        if session_id is None:
            return
        with self._lock:
            self._session_ids.add(session_id)

    def snapshot_and_reset(self) -> MetricsSnapshot:
        """Take a snapshot of current counters and reset them to zero."""
        with self._lock:
            snapshot = MetricsSnapshot(
                requests_accepted=self._requests_accepted,
                requests_completed=self._requests_completed,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                streaming_requests=self._streaming_requests,
                non_streaming_requests=self._non_streaming_requests,
                sessions_with_ids=len(self._session_ids),
            )
            self._requests_accepted = 0
            self._requests_completed = 0
            self._input_tokens = 0
            self._output_tokens = 0
            self._streaming_requests = 0
            self._non_streaming_requests = 0
            self._session_ids = set()
            return snapshot
```

**Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_collector.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add src/luthien_proxy/usage_telemetry/ tests/unit_tests/usage_telemetry/
git commit -m "feat: add UsageCollector for in-memory usage metrics"
```

---

### Task 4: Config — telemetry enabled resolution and deployment_id

**Files:**
- Create: `src/luthien_proxy/usage_telemetry/config.py`
- Create: `tests/unit_tests/usage_telemetry/test_config.py`

**Step 1: Write the failing tests**

Create `tests/unit_tests/usage_telemetry/test_config.py`:

```python
"""Tests for telemetry config resolution."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.usage_telemetry.config import TelemetryConfig, resolve_telemetry_config


class TestResolveConfig:
    @pytest.mark.asyncio
    async def test_env_true_overrides_db_false(self):
        """Env var takes precedence over DB value."""
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(return_value={
            "enabled": False,
            "deployment_id": uuid.uuid4(),
        })

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=True)
        assert config.enabled is True

    @pytest.mark.asyncio
    async def test_env_false_overrides_db_true(self):
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(return_value={
            "enabled": True,
            "deployment_id": uuid.uuid4(),
        })

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=False)
        assert config.enabled is False

    @pytest.mark.asyncio
    async def test_no_env_uses_db_value(self):
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(return_value={
            "enabled": False,
            "deployment_id": uuid.uuid4(),
        })

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=None)
        assert config.enabled is False

    @pytest.mark.asyncio
    async def test_no_env_no_db_defaults_enabled(self):
        """When nothing is configured, telemetry is enabled by default."""
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(return_value={
            "enabled": None,
            "deployment_id": uuid.uuid4(),
        })

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=None)
        assert config.enabled is True

    @pytest.mark.asyncio
    async def test_deployment_id_from_db(self):
        dep_id = uuid.uuid4()
        db_pool = MagicMock()
        pool = AsyncMock()
        db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(return_value={
            "enabled": None,
            "deployment_id": dep_id,
        })

        config = await resolve_telemetry_config(db_pool=db_pool, env_value=None)
        assert config.deployment_id == str(dep_id)

    @pytest.mark.asyncio
    async def test_no_db_pool_defaults_enabled_with_random_id(self):
        config = await resolve_telemetry_config(db_pool=None, env_value=None)
        assert config.enabled is True
        assert config.deployment_id  # non-empty string
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_config.py -v`
Expected: FAIL — module not found.

**Step 3: Write the config module**

Create `src/luthien_proxy/usage_telemetry/config.py`:

```python
"""Telemetry configuration resolution.

Precedence: env var > DB stored value > default (enabled).
This is intentionally the OPPOSITE of the auth_config pattern where DB
overrides env. Here the env var is the hard override so operators can
disable telemetry without touching the DB.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


@dataclass
class TelemetryConfig:
    enabled: bool
    deployment_id: str


async def resolve_telemetry_config(
    *,
    db_pool: DatabasePool | None,
    env_value: bool | None,
) -> TelemetryConfig:
    """Resolve telemetry config from env var and DB.

    Args:
        db_pool: Database pool (may be None in minimal setups)
        env_value: Value from USAGE_TELEMETRY env var (None if not set)

    Returns:
        Resolved telemetry configuration
    """
    db_enabled: bool | None = None
    deployment_id: str = str(uuid.uuid4())

    if db_pool is not None:
        try:
            pool = await db_pool.get_pool()
            row = await pool.fetchrow("SELECT enabled, deployment_id FROM telemetry_config WHERE id = 1")
            if row:
                db_enabled = row["enabled"]
                deployment_id = str(row["deployment_id"])
        except Exception:
            logger.warning("Failed to read telemetry_config from DB, using defaults", exc_info=True)

    # Precedence: env var > DB > default (enabled)
    if env_value is not None:
        enabled = env_value
    elif db_enabled is not None:
        enabled = db_enabled
    else:
        enabled = True

    return TelemetryConfig(enabled=enabled, deployment_id=deployment_id)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_config.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/luthien_proxy/usage_telemetry/config.py tests/unit_tests/usage_telemetry/test_config.py
git commit -m "feat: add telemetry config resolution (env > DB > default)"
```

---

### Task 5: Sender — periodic rollup posting

**Files:**
- Create: `src/luthien_proxy/usage_telemetry/sender.py`
- Create: `tests/unit_tests/usage_telemetry/test_sender.py`

**Step 1: Write the failing tests**

Create `tests/unit_tests/usage_telemetry/test_sender.py`:

```python
"""Tests for telemetry sender."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.usage_telemetry.collector import UsageCollector
from luthien_proxy.usage_telemetry.config import TelemetryConfig
from luthien_proxy.usage_telemetry.sender import TelemetrySender, build_payload


class TestBuildPayload:
    def test_payload_structure(self):
        config = TelemetryConfig(enabled=True, deployment_id="test-uuid")
        collector = UsageCollector()
        collector.record_accepted()
        collector.record_tokens(input_tokens=100, output_tokens=50)

        metrics = collector.snapshot_and_reset()
        payload = build_payload(config=config, metrics=metrics, interval_seconds=300)

        assert payload["schema_version"] == 1
        assert payload["deployment_id"] == "test-uuid"
        assert payload["interval_seconds"] == 300
        assert payload["metrics"]["requests_accepted"] == 1
        assert payload["metrics"]["input_tokens"] == 100
        assert "proxy_version" in payload
        assert "python_version" in payload
        assert "timestamp" in payload


class TestTelemetrySender:
    @pytest.mark.asyncio
    async def test_send_posts_to_endpoint(self):
        config = TelemetryConfig(enabled=True, deployment_id="test-uuid")
        collector = UsageCollector()
        collector.record_accepted()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        sender = TelemetrySender(
            config=config,
            collector=collector,
            endpoint="https://test.example.com/v1/events",
            interval_seconds=300,
        )

        with patch("luthien_proxy.usage_telemetry.sender.httpx.AsyncClient", return_value=mock_client):
            await sender.send_once()

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://test.example.com/v1/events"
        posted_data = call_args[1]["json"]
        assert posted_data["metrics"]["requests_accepted"] == 1

    @pytest.mark.asyncio
    async def test_send_disabled_does_nothing(self):
        config = TelemetryConfig(enabled=False, deployment_id="test-uuid")
        collector = UsageCollector()
        collector.record_accepted()

        sender = TelemetrySender(
            config=config,
            collector=collector,
            endpoint="https://test.example.com/v1/events",
            interval_seconds=300,
        )

        with patch("luthien_proxy.usage_telemetry.sender.httpx.AsyncClient") as mock_cls:
            await sender.send_once()
            mock_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_failure_logs_and_continues(self):
        """Network errors should be logged, not raised."""
        config = TelemetryConfig(enabled=True, deployment_id="test-uuid")
        collector = UsageCollector()
        collector.record_accepted()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        sender = TelemetrySender(
            config=config,
            collector=collector,
            endpoint="https://test.example.com/v1/events",
            interval_seconds=300,
        )

        with patch("luthien_proxy.usage_telemetry.sender.httpx.AsyncClient", return_value=mock_client):
            await sender.send_once()  # should not raise

    @pytest.mark.asyncio
    async def test_skips_empty_intervals(self):
        """Don't send if no requests were recorded."""
        config = TelemetryConfig(enabled=True, deployment_id="test-uuid")
        collector = UsageCollector()  # no data recorded

        sender = TelemetrySender(
            config=config,
            collector=collector,
            endpoint="https://test.example.com/v1/events",
            interval_seconds=300,
        )

        with patch("luthien_proxy.usage_telemetry.sender.httpx.AsyncClient") as mock_cls:
            await sender.send_once()
            mock_cls.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_sender.py -v`
Expected: FAIL — module not found.

**Step 3: Write the sender**

Create `src/luthien_proxy/usage_telemetry/sender.py`:

```python
"""Periodic telemetry sender.

Snapshots the collector every interval and POSTs the rollup to the
configured endpoint. Failures are logged and discarded — no retry storms.
"""

from __future__ import annotations

import asyncio
import logging
import platform
from datetime import UTC, datetime
from importlib.metadata import version as pkg_version
from typing import Any

import httpx

from luthien_proxy.usage_telemetry.collector import MetricsSnapshot, UsageCollector
from luthien_proxy.usage_telemetry.config import TelemetryConfig

logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 10


def _get_proxy_version() -> str:
    try:
        return pkg_version("luthien-proxy")
    except Exception:
        return "unknown"


def build_payload(
    *,
    config: TelemetryConfig,
    metrics: MetricsSnapshot,
    interval_seconds: int,
) -> dict[str, Any]:
    """Build the JSON payload for a single rollup interval."""
    return {
        "schema_version": 1,
        "deployment_id": config.deployment_id,
        "proxy_version": _get_proxy_version(),
        "python_version": platform.python_version(),
        "interval_seconds": interval_seconds,
        "timestamp": datetime.now(UTC).isoformat(),
        "metrics": dict(metrics),
    }


class TelemetrySender:
    """Periodically sends usage rollups to the telemetry endpoint."""

    def __init__(
        self,
        *,
        config: TelemetryConfig,
        collector: UsageCollector,
        endpoint: str,
        interval_seconds: int = 300,
    ) -> None:
        self._config = config
        self._collector = collector
        self._endpoint = endpoint
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def send_once(self) -> None:
        """Snapshot counters and send if telemetry is enabled and data exists."""
        if not self._config.enabled:
            return

        metrics = self._collector.snapshot_and_reset()

        # Skip empty intervals
        if metrics["requests_accepted"] == 0:
            return

        payload = build_payload(
            config=self._config,
            metrics=metrics,
            interval_seconds=self._interval_seconds,
        )

        try:
            async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) as client:
                response = await client.post(self._endpoint, json=payload)
                if response.status_code >= 400:
                    logger.debug("Telemetry endpoint returned %d", response.status_code)
        except Exception:
            logger.debug("Failed to send telemetry", exc_info=True)

    async def _run_loop(self) -> None:
        """Periodic send loop. Runs until cancelled."""
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self.send_once()

    def start(self) -> None:
        """Start the periodic send loop as a background task."""
        if not self._config.enabled:
            logger.info("Usage telemetry disabled")
            return
        logger.info(
            "Usage telemetry enabled (interval=%ds, endpoint=%s)",
            self._interval_seconds,
            self._endpoint,
        )
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Cancel the loop and flush final interval."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Flush whatever accumulated since last send
        await self.send_once()
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_sender.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/luthien_proxy/usage_telemetry/sender.py tests/unit_tests/usage_telemetry/test_sender.py
git commit -m "feat: add TelemetrySender for periodic rollup posting"
```

---

### Task 6: Wire into Dependencies and app lifespan

**Files:**
- Modify: `src/luthien_proxy/dependencies.py:22-40` (add collector field)
- Modify: `src/luthien_proxy/main.py:79-163` (lifespan — create collector, sender, start/stop)
- Create: `tests/unit_tests/usage_telemetry/test_lifecycle.py`

**Step 1: Write the failing test**

Create `tests/unit_tests/usage_telemetry/test_lifecycle.py`:

```python
"""Tests for telemetry lifecycle wiring."""

from luthien_proxy.usage_telemetry.collector import UsageCollector
from luthien_proxy.dependencies import Dependencies


class TestDependenciesHasCollector:
    def test_collector_defaults_to_none(self):
        """Dependencies.usage_collector should exist and default to None."""
        # Minimal dependencies with required fields mocked
        from unittest.mock import MagicMock
        deps = Dependencies(
            db_pool=None,
            redis_client=None,
            llm_client=MagicMock(),
            policy_manager=MagicMock(),
            emitter=MagicMock(),
            api_key="test",
            admin_key=None,
        )
        assert deps.usage_collector is None

    def test_collector_can_be_set(self):
        from unittest.mock import MagicMock
        collector = UsageCollector()
        deps = Dependencies(
            db_pool=None,
            redis_client=None,
            llm_client=MagicMock(),
            policy_manager=MagicMock(),
            emitter=MagicMock(),
            api_key="test",
            admin_key=None,
            usage_collector=collector,
        )
        assert deps.usage_collector is collector
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_lifecycle.py -v`
Expected: FAIL — `usage_collector` not a field on Dependencies.

**Step 3: Add collector to Dependencies**

In `src/luthien_proxy/dependencies.py`, add the import at top:

```python
from luthien_proxy.usage_telemetry.collector import UsageCollector
```

Add field after `enable_request_logging` (line ~40):

```python
    usage_collector: UsageCollector | None = field(default=None)
```

Add dependency function after `require_credential_manager`:

```python
def get_usage_collector(request: Request) -> UsageCollector | None:
    """Get usage telemetry collector from dependencies."""
    return get_dependencies(request).usage_collector
```

Add to `__all__`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_lifecycle.py -v`
Expected: PASS

**Step 5: Wire into `main.py` lifespan**

In `src/luthien_proxy/main.py` `lifespan()`, after CredentialManager init (around line 132) and before the Dependencies creation (around line 140):

Add imports at top of file:

```python
from luthien_proxy.usage_telemetry.collector import UsageCollector
from luthien_proxy.usage_telemetry.config import resolve_telemetry_config
from luthien_proxy.usage_telemetry.sender import TelemetrySender
```

Add in lifespan, before Dependencies creation:

```python
        # Initialize usage telemetry
        _settings = get_settings()
        _telemetry_config = await resolve_telemetry_config(
            db_pool=db_pool,
            env_value=_settings.usage_telemetry,
        )
        _usage_collector = UsageCollector()
        _telemetry_sender = TelemetrySender(
            config=_telemetry_config,
            collector=_usage_collector,
            endpoint=_settings.telemetry_endpoint,
        )
        _telemetry_sender.start()
        logger.info(f"Usage telemetry: enabled={_telemetry_config.enabled}")
```

Add `usage_collector=_usage_collector` to the Dependencies constructor call.

In the shutdown section (after `yield`), add before the shutdown log:

```python
        await _telemetry_sender.stop()
```

**Step 6: Run existing tests to verify no breakage**

Run: `uv run pytest tests/unit_tests/ -x -q`
Expected: All existing tests still pass.

**Step 7: Commit**

```bash
git add src/luthien_proxy/dependencies.py src/luthien_proxy/main.py tests/unit_tests/usage_telemetry/test_lifecycle.py
git commit -m "feat: wire usage telemetry into Dependencies and app lifespan"
```

---

### Task 7: Integration — increment counters in pipeline

**Files:**
- Modify: `src/luthien_proxy/pipeline/processor.py` (~lines 104, 358, 414)
- Modify: `src/luthien_proxy/pipeline/anthropic_processor.py` (~lines 322, 557, 587, 647-670)
- Modify: `src/luthien_proxy/gateway_routes.py` (~lines 126-164)

This task wires the collector into the actual request processing pipeline. The key insight from Codex's review: streaming requests complete in `finally` blocks inside generators, not when the handler returns. So counter increments must happen in those `finally` blocks.

**Step 1: Pass collector through gateway routes**

In `src/luthien_proxy/gateway_routes.py`, add import:

```python
from luthien_proxy.dependencies import get_usage_collector
from luthien_proxy.usage_telemetry.collector import UsageCollector
```

Modify `chat_completions` (line ~126) to pass collector:

```python
@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    _: str = Depends(verify_token),
    policy: OpenAIPolicyInterface = Depends(get_policy),
    llm_client: LLMClient = Depends(get_llm_client),
    emitter: EventEmitterProtocol = Depends(get_emitter),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """OpenAI-compatible chat completions endpoint."""
    deps = get_dependencies(request)
    return await process_llm_request(
        request=request,
        policy=policy,
        llm_client=llm_client,
        emitter=emitter,
        db_pool=db_pool,
        enable_request_logging=deps.enable_request_logging,
        usage_collector=deps.usage_collector,
    )
```

Modify `anthropic_messages` (line ~147) similarly:

```python
@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    anthropic_client: AnthropicClient = Depends(resolve_anthropic_client),
    anthropic_policy: AnthropicExecutionInterface = Depends(get_anthropic_policy),
    emitter: EventEmitterProtocol = Depends(get_emitter),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """Anthropic Messages API endpoint (native Anthropic path)."""
    deps = get_dependencies(request)
    return await process_anthropic_request(
        request=request,
        policy=anthropic_policy,
        anthropic_client=anthropic_client,
        emitter=emitter,
        db_pool=db_pool,
        enable_request_logging=deps.enable_request_logging,
        usage_collector=deps.usage_collector,
    )
```

**Step 2: Add collector param to `process_llm_request`**

In `src/luthien_proxy/pipeline/processor.py`, add import:

```python
from luthien_proxy.usage_telemetry.collector import UsageCollector
```

Add `usage_collector: UsageCollector | None = None` parameter to `process_llm_request()` (line ~82).

After `call_id = str(uuid.uuid4())` (line ~104), add:

```python
    if usage_collector:
        usage_collector.record_accepted()
        usage_collector.record_session(session_id)
```

Wait — `session_id` is extracted later (line ~113). Move the `record_session` call to after session_id extraction. Place `record_accepted()` right after the span starts (line ~108). Place `record_session()` after line ~123 where `session_id` is available.

In `_handle_streaming`, the `finally` block (line ~352) is where streaming completes. Add to that `finally`:

```python
                    if usage_collector and (error_status is None):
                        usage_collector.record_completed(is_streaming=True)
```

In `_handle_non_streaming` (line ~374), after the JSONResponse is built (before `return`), add:

```python
        if usage_collector:
            usage_collector.record_completed(is_streaming=False)
```

Note: `usage_collector` must be threaded through to `_handle_streaming` and `_handle_non_streaming` as a parameter.

**Step 3: Add collector param to `process_anthropic_request`**

In `src/luthien_proxy/pipeline/anthropic_processor.py`, add import:

```python
from luthien_proxy.usage_telemetry.collector import UsageCollector
```

Add `usage_collector: UsageCollector | None = None` parameter to `process_anthropic_request()` (line ~298).

After `call_id = str(uuid.uuid4())` (line ~322), add:

```python
    if usage_collector:
        usage_collector.record_accepted()
```

After `session_id` is extracted (line ~436), add:

```python
    if usage_collector:
        usage_collector.record_session(session_id)
```

Thread `usage_collector` through to `_execute_anthropic_policy`, then to `_handle_execution_streaming` and `_handle_execution_non_streaming`.

In `_handle_execution_streaming` `finally` block (line ~557), add:

```python
                    if usage_collector and final_status == 200:
                        usage_collector.record_completed(is_streaming=True)
```

In `_handle_execution_non_streaming`, after the JSONResponse is built (around line ~670), add:

```python
    if usage_collector:
        usage_collector.record_completed(is_streaming=False)
```

**Step 4: Add token counting for Anthropic non-streaming**

In `_handle_execution_non_streaming`, after `final_response` is confirmed not None (line ~630):

```python
    if usage_collector and "usage" in final_response:
        usage = final_response["usage"]
        usage_collector.record_tokens(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
```

**Step 5: Add token counting for Anthropic streaming**

In `_handle_execution_streaming` `finally` block, after reconstruction (line ~571):

```python
                    if usage_collector and reconstructed is not None and "usage" in reconstructed:
                        usage = reconstructed["usage"]
                        usage_collector.record_tokens(
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                        )
```

**Step 6: Run all unit tests**

Run: `uv run pytest tests/unit_tests/ -x -q`
Expected: All pass. Many existing tests mock out dependencies; since `usage_collector` defaults to None, they should be unaffected.

**Step 7: Commit**

```bash
git add src/luthien_proxy/pipeline/processor.py src/luthien_proxy/pipeline/anthropic_processor.py src/luthien_proxy/gateway_routes.py
git commit -m "feat: wire usage telemetry counters into request pipelines"
```

---

### Task 8: Admin API — telemetry config endpoints

**Files:**
- Modify: `src/luthien_proxy/admin/routes.py`
- Create: `tests/unit_tests/usage_telemetry/test_admin_api.py`

**Step 1: Write the failing tests**

Create `tests/unit_tests/usage_telemetry/test_admin_api.py`:

```python
"""Tests for telemetry admin API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_telemetry():
    """Create a minimal FastAPI app with telemetry admin routes."""
    from luthien_proxy.admin import router as admin_router

    app = FastAPI()
    app.include_router(admin_router)

    mock_deps = MagicMock()
    mock_deps.admin_key = "test-admin-key"
    mock_deps.db_pool = MagicMock()
    app.state.dependencies = mock_deps

    return app, mock_deps


class TestGetTelemetryConfig:
    def test_returns_config(self, app_with_telemetry):
        app, mock_deps = app_with_telemetry
        pool = AsyncMock()
        mock_deps.db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(return_value={
            "enabled": True,
            "deployment_id": "test-uuid",
            "updated_at": None,
            "updated_by": None,
        })

        client = TestClient(app)
        response = client.get(
            "/api/admin/telemetry",
            headers={"Authorization": "Bearer test-admin-key"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["deployment_id"] == "test-uuid"


class TestUpdateTelemetryConfig:
    def test_updates_enabled(self, app_with_telemetry):
        app, mock_deps = app_with_telemetry
        pool = AsyncMock()
        mock_deps.db_pool.get_pool = AsyncMock(return_value=pool)
        pool.fetchrow = AsyncMock(return_value={
            "enabled": False,
            "deployment_id": "test-uuid",
            "updated_at": "2026-03-05",
            "updated_by": "admin-api",
        })
        pool.execute = AsyncMock()

        client = TestClient(app)
        response = client.put(
            "/api/admin/telemetry",
            json={"enabled": False},
            headers={"Authorization": "Bearer test-admin-key"},
        )
        assert response.status_code == 200
        pool.execute.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_admin_api.py -v`
Expected: FAIL — no matching routes.

**Step 3: Add admin endpoints**

In `src/luthien_proxy/admin/routes.py`, add after the auth config endpoints:

```python
# --- Telemetry Config ---

class TelemetryConfigResponse(BaseModel):
    enabled: bool | None
    deployment_id: str
    updated_at: str | None = None
    updated_by: str | None = None


class TelemetryConfigUpdate(BaseModel):
    enabled: bool


@router.get("/telemetry", response_model=TelemetryConfigResponse)
async def get_telemetry_config(
    _: str = Depends(verify_admin_token),
    db_pool: "db.DatabasePool | None" = Depends(get_db_pool),
):
    """Get current telemetry configuration."""
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    pool = await db_pool.get_pool()
    row = await pool.fetchrow("SELECT * FROM telemetry_config WHERE id = 1")
    if not row:
        raise HTTPException(status_code=404, detail="Telemetry config not initialized")
    return TelemetryConfigResponse(
        enabled=row["enabled"],
        deployment_id=str(row["deployment_id"]),
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
        updated_by=row["updated_by"],
    )


@router.put("/telemetry", response_model=TelemetryConfigResponse)
async def update_telemetry_config(
    body: TelemetryConfigUpdate,
    _: str = Depends(verify_admin_token),
    db_pool: "db.DatabasePool | None" = Depends(get_db_pool),
):
    """Update telemetry enabled state in DB."""
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")
    pool = await db_pool.get_pool()
    await pool.execute(
        "UPDATE telemetry_config SET enabled = $1, updated_at = now(), updated_by = 'admin-api' WHERE id = 1",
        body.enabled,
    )
    row = await pool.fetchrow("SELECT * FROM telemetry_config WHERE id = 1")
    return TelemetryConfigResponse(
        enabled=row["enabled"],
        deployment_id=str(row["deployment_id"]),
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
        updated_by=row["updated_by"],
    )
```

Add necessary imports (`get_db_pool` from dependencies if not already imported).

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit_tests/usage_telemetry/test_admin_api.py -v`
Expected: PASS (may need to adjust mocks for actual route resolution)

**Step 5: Commit**

```bash
git add src/luthien_proxy/admin/routes.py tests/unit_tests/usage_telemetry/test_admin_api.py
git commit -m "feat: add GET/PUT /api/admin/telemetry endpoints"
```

---

### Task 9: `quick_start.sh` — first-run telemetry prompt

**Files:**
- Modify: `scripts/quick_start.sh` (after services are up, ~line 183)

**Step 1: Add the prompt logic**

After the "services healthy" check (around line 183), before the final output block, add:

```bash
# Telemetry opt-in prompt (only if env var is not set and DB has no stored value)
if [ -z "$USAGE_TELEMETRY" ]; then
    # Check if DB already has a telemetry preference
    telemetry_enabled=$(docker compose exec -T gateway curl -s -H "Authorization: Bearer ${ADMIN_API_KEY}" http://localhost:${GATEWAY_PORT:-8000}/api/admin/telemetry 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('enabled',''))" 2>/dev/null)
    if [ "$telemetry_enabled" = "None" ] || [ -z "$telemetry_enabled" ]; then
        echo ""
        echo "📊 Anonymous usage telemetry"
        echo "   Luthien collects aggregate usage metrics (request counts, token counts)"
        echo "   to understand how the proxy is used. No model names, API keys, IPs,"
        echo "   or content is ever sent."
        echo ""
        read -r -p "   Send anonymous usage data? [Y/n] " telemetry_choice
        telemetry_choice=${telemetry_choice:-Y}
        if [[ "$telemetry_choice" =~ ^[Yy] ]]; then
            docker compose exec -T gateway curl -s -X PUT \
                -H "Authorization: Bearer ${ADMIN_API_KEY}" \
                -H "Content-Type: application/json" \
                -d '{"enabled": true}' \
                http://localhost:${GATEWAY_PORT:-8000}/api/admin/telemetry > /dev/null 2>&1
            echo "   ✅ Telemetry enabled. Change anytime at /credentials or set USAGE_TELEMETRY=false"
        else
            docker compose exec -T gateway curl -s -X PUT \
                -H "Authorization: Bearer ${ADMIN_API_KEY}" \
                -H "Content-Type: application/json" \
                -d '{"enabled": false}' \
                http://localhost:${GATEWAY_PORT:-8000}/api/admin/telemetry > /dev/null 2>&1
            echo "   ℹ️  Telemetry disabled. Change anytime at /credentials or set USAGE_TELEMETRY=true"
        fi
    fi
fi
```

**Step 2: Test manually**

Run `./scripts/quick_start.sh` and verify the prompt appears on first run, and doesn't appear on subsequent runs.

**Step 3: Commit**

```bash
git add scripts/quick_start.sh
git commit -m "feat: add telemetry opt-in prompt to quick_start.sh"
```

---

### Task 10: Update `__init__.py` exports and run dev checks

**Files:**
- Modify: `src/luthien_proxy/usage_telemetry/__init__.py`

**Step 1: Update exports**

```python
"""Anonymous usage telemetry — aggregate metrics sent to central endpoint."""

from luthien_proxy.usage_telemetry.collector import UsageCollector
from luthien_proxy.usage_telemetry.config import TelemetryConfig, resolve_telemetry_config
from luthien_proxy.usage_telemetry.sender import TelemetrySender

__all__ = [
    "UsageCollector",
    "TelemetryConfig",
    "TelemetrySender",
    "resolve_telemetry_config",
]
```

**Step 2: Run full dev checks**

Run: `./scripts/dev_checks.sh`
Expected: All formatting, linting, type checks, and tests pass.

**Step 3: Fix any issues found by dev checks**

Address any ruff, pyright, or test failures.

**Step 4: Commit**

```bash
git add -A
git commit -m "chore: finalize usage telemetry module exports and fix lint"
```

---

### Task 11: Update CHANGELOG and clean up

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `dev/OBJECTIVE.md`

**Step 1: Update CHANGELOG**

Add entry under the appropriate section:

```markdown
### Added
- Anonymous usage telemetry: aggregate metrics (request counts, token counts, session counts) sent periodically to configurable endpoint. Opt-out via `USAGE_TELEMETRY=false` env var or admin UI toggle. No identifiable data is sent.
- `GET/PUT /api/admin/telemetry` endpoints for managing telemetry configuration
- First-run telemetry prompt in `quick_start.sh`
- DB migration for `telemetry_config` table
```

**Step 2: Commit**

```bash
git add CHANGELOG.md dev/OBJECTIVE.md
git commit -m "docs: update CHANGELOG for usage telemetry feature"
```

---

## Summary

| Task | Description | Est. complexity |
|------|-------------|----------------|
| 1 | DB migration | Trivial |
| 2 | Settings fields | Simple |
| 3 | Collector (counters) | Simple |
| 4 | Config resolution | Simple |
| 5 | Sender (periodic POST) | Medium |
| 6 | Wire into Dependencies/lifespan | Medium |
| 7 | Pipeline integration (counters) | Medium-complex |
| 8 | Admin API endpoints | Simple |
| 9 | quick_start.sh prompt | Simple |
| 10 | Exports + dev checks | Trivial |
| 11 | CHANGELOG | Trivial |

Tasks 1-5 are independent and can be parallelized. Tasks 6-7 depend on 1-5. Tasks 8-9 depend on 6. Task 10-11 are final cleanup.
