"""sqlite_e2e tests for the /metrics Prometheus endpoint.

Uses the shared sqlite gateway fixtures from conftest.py — no custom
gateway or mock server setup needed.

Run:  uv run pytest tests/luthien_proxy/e2e_tests/sqlite/test_metrics_endpoint.py -v --timeout=60
"""

import httpx
import pytest
from prometheus_client.parser import text_string_to_metric_families
from tests.luthien_proxy.e2e_tests.mock_anthropic.responses import text_response

pytestmark = pytest.mark.sqlite_e2e

_EXPECTED_FAMILIES = {
    "luthien_requests_completed",
    "luthien_tokens",
    "luthien_request_ttfb_seconds",
    "luthien_active_requests",
}

_TTFB_BUCKET_BOUNDARIES = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0)


def _parse_families(body: str) -> dict[str, object]:
    """Parse Prometheus text format into {name: MetricFamily}."""
    return {f.name: f for f in text_string_to_metric_families(body)}


async def test_metrics_returns_200_before_traffic(gateway_url):
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{gateway_url}/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]


async def test_metrics_contains_expected_families_after_request(gateway_url, api_key, mock_anthropic):
    mock_anthropic.enqueue(text_response("metrics test"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{gateway_url}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 100,
                "stream": False,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )
        assert resp.status_code == 200, f"Gateway returned {resp.status_code}: {resp.text[:500]}"

        metrics_resp = await client.get(f"{gateway_url}/metrics")
        families = _parse_families(metrics_resp.text)

        for name in _EXPECTED_FAMILIES:
            assert name in families, f"Missing metric family {name!r}. Got: {sorted(families)}"


async def test_metrics_no_double_suffixes(gateway_url, api_key, mock_anthropic):
    mock_anthropic.enqueue(text_response("suffix test"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            f"{gateway_url}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 100,
                "stream": False,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )

        families = _parse_families((await client.get(f"{gateway_url}/metrics")).text)

        for name in families:
            assert not name.endswith("_total_total"), f"Double _total suffix: {name}"
            assert not name.endswith("_seconds_seconds"), f"Double _seconds suffix: {name}"


async def test_metrics_histogram_buckets(gateway_url, api_key, mock_anthropic):
    """Custom TTFB histogram buckets are applied (not OTel ms-oriented defaults)."""
    mock_anthropic.enqueue(text_response("bucket test"))

    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            f"{gateway_url}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 100,
                "stream": False,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )

        families = _parse_families((await client.get(f"{gateway_url}/metrics")).text)
        ttfb = families.get("luthien_request_ttfb_seconds")
        assert ttfb is not None, "Missing luthien_request_ttfb_seconds family"

        bucket_les = sorted(
            float(s.labels["le"]) for s in ttfb.samples if s.name.endswith("_bucket") and s.labels.get("le") != "+Inf"
        )
        expected = sorted(_TTFB_BUCKET_BOUNDARIES)
        assert bucket_les == expected, f"Bucket boundaries mismatch: got {bucket_les}, expected {expected}"


async def test_metrics_no_auth_required(gateway_url):
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{gateway_url}/metrics")
        assert resp.status_code == 200
