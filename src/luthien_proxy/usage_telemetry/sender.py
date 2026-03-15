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
    except Exception as e:
        logger.debug(f"Could not determine proxy version: {repr(e)}")
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
        """Initialize sender with config, collector, and endpoint."""
        self._config = config
        self._collector = collector
        self._endpoint = endpoint
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def send_once(self) -> None:
        """Snapshot counters and send if data exists."""
        metrics = self._collector.snapshot_and_reset()

        has_data = (
            metrics["requests_accepted"] > 0
            or metrics["requests_completed"] > 0
            or metrics["input_tokens"] > 0
            or metrics["output_tokens"] > 0
        )
        if not has_data:
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
