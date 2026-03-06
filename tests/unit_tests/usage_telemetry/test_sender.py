"""Tests for telemetry sender."""

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

    @pytest.mark.asyncio
    async def test_sends_cross_interval_completions(self):
        """Completions from requests accepted in a prior interval should still be sent."""
        config = TelemetryConfig(enabled=True, deployment_id="test-uuid")
        collector = UsageCollector()

        # Request was accepted in a previous interval (already snapshotted),
        # but completes in this interval
        collector.record_completed(is_streaming=True)
        collector.record_tokens(input_tokens=500, output_tokens=200)

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
        posted_data = mock_client.post.call_args[1]["json"]
        assert posted_data["metrics"]["requests_accepted"] == 0
        assert posted_data["metrics"]["requests_completed"] == 1
        assert posted_data["metrics"]["input_tokens"] == 500
        assert posted_data["metrics"]["output_tokens"] == 200
