"""Integration tests hitting the FastAPI gateway endpoints."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from luthien_proxy.main import create_app
from luthien_proxy.messages import Request
from luthien_proxy.policies.simple_policy import SimplePolicy

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.environ.get("OPENAI_API_KEY") and os.environ.get("ANTHROPIC_API_KEY")),
        reason="Requires OPENAI_API_KEY and ANTHROPIC_API_KEY for live LiteLLM calls.",
    ),
]


class UppercasePolicy(SimplePolicy):
    """Test policy that uppercases all content."""

    async def on_response_content(self, content: str, request: Request) -> str:
        """Uppercase all content."""
        return content.upper()


def test_openai_streaming_with_policy():
    """Integration test: OpenAI streaming endpoint with uppercase policy."""
    # Create app with uppercase policy
    app = create_app(
        api_key="test-key",
        database_url="",  # No DB needed for E2E test
        redis_url="",  # No Redis needed for E2E test
        policy=UppercasePolicy(),
    )

    with TestClient(app) as client:
        # Make streaming request
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Say hello in 3 words"}],
                "max_tokens": 20,
                "stream": True,
            },
            headers={"Authorization": "Bearer test-key"},
        )

        # Verify response
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        # Collect SSE data
        lines = response.text.split("\n")
        data_lines = [line for line in lines if line.startswith("data: ")]

        # Should have at least some data
        assert len(data_lines) > 0, "Should receive SSE data"

        # TODO: Parse SSE and verify content is uppercase
        # This requires parsing the SSE format and extracting content from deltas


def test_anthropic_streaming():
    """Integration test: Anthropic streaming endpoint."""
    app = create_app(
        api_key="test-key",
        database_url="",
        redis_url="",
        policy=SimplePolicy(),
    )

    with TestClient(app) as client:
        # Make streaming request
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "Say hello"}],
                "max_tokens": 20,
                "stream": True,
            },
            headers={"Authorization": "Bearer test-key"},
        )

        # Verify response
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        # Collect SSE events
        lines = response.text.split("\n")
        event_lines = [line for line in lines if line.startswith("event: ")]

        # Should have Anthropic-specific events
        assert len(event_lines) > 0, "Should receive SSE events"


def test_openai_non_streaming():
    """Integration test: OpenAI non-streaming endpoint."""
    app = create_app(
        api_key="test-key",
        database_url="",
        redis_url="",
        policy=SimplePolicy(),
    )

    with TestClient(app) as client:
        # Make non-streaming request
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Say hello"}],
                "max_tokens": 20,
                "stream": False,
            },
            headers={"Authorization": "Bearer test-key"},
        )

        # Verify response
        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert "choices" in data
        assert len(data["choices"]) > 0
        assert "message" in data["choices"][0]
        assert "content" in data["choices"][0]["message"]


@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/messages"])
def test_request_size_limit(endpoint):
    """Test that oversized requests are rejected."""
    app = create_app(
        api_key="test-key",
        database_url="",
        redis_url="",
        policy=SimplePolicy(),
    )

    with TestClient(app) as client:
        # Create a payload larger than 10MB
        large_content = "x" * (11 * 1024 * 1024)  # 11MB
        response = client.post(
            endpoint,
            json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": large_content}]},
            headers={"Authorization": "Bearer test-key"},
        )

        assert response.status_code == 413
        assert "too large" in response.json()["detail"].lower()
