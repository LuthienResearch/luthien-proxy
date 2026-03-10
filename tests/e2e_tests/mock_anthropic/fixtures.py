"""Pytest fixtures for mock Anthropic server."""

import pytest
from tests.e2e_tests.mock_anthropic.server import MockAnthropicServer  # type: ignore[import]


@pytest.fixture(scope="session")
def mock_anthropic():
    """Session-scoped mock Anthropic server.

    The server runs in a background thread (its own event loop) so it stays
    responsive regardless of what pytest-asyncio's event loop is doing.

    Use ``mock_anthropic.enqueue(response)`` before each test to control
    what the mock returns for that request.

    Requires the gateway to be started with ANTHROPIC_BASE_URL pointing at
    this server. See docker-compose.mock.yaml.
    """
    server = MockAnthropicServer()
    server.start()
    yield server
    server.stop()
