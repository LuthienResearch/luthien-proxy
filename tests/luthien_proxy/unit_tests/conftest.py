# ABOUTME: Shared test fixtures for unit tests
# ABOUTME: Provides network blocking, streaming chunk helpers, and common test utilities

"""Shared fixtures for unit tests."""

import socket

import pytest
from tests.constants import DEFAULT_TEST_MODEL

_original_socket = socket.socket


class BlockedSocketError(Exception):
    """Raised when a unit test attempts to create a network socket."""

    pass


def _guarded_socket(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, fileno=None):
    """Socket wrapper that blocks network sockets but allows Unix sockets.

    This prevents unit tests from accidentally making network calls to
    Redis, PostgreSQL, or LLM APIs while still allowing asyncio's
    internal Unix socket pairs.
    """
    # Allow Unix sockets (AF_UNIX) for asyncio event loop
    if family == socket.AF_UNIX:
        return _original_socket(family, type, proto, fileno)

    # Block all network sockets (AF_INET, AF_INET6)
    raise BlockedSocketError(
        f"Unit test attempted to create network socket (family={family}). "
        "Network calls are blocked in unit tests. Use mocks instead."
    )


@pytest.fixture(autouse=True)
def _block_network_sockets(monkeypatch):
    """Block network socket access for all unit tests.

    Prevents any test from accidentally making real network connections
    to Redis, PostgreSQL, or LLM APIs. Unix sockets are still allowed
    for asyncio event loop.
    """
    monkeypatch.setattr(socket, "socket", _guarded_socket)
    yield


class _StreamingChunk:
    """Mock streaming chunk object for testing."""

    def __init__(
        self,
        content: str | None = None,
        id: str = "chatcmpl-123",
        model: str = DEFAULT_TEST_MODEL,
        finish_reason: str | None = None,
    ):
        self.id = id
        self.model = model
        self.content = content

        class Delta:
            def __init__(self, content):
                self.content = content

        class Choice:
            def __init__(self, delta, finish_reason):
                self.delta = delta
                self.finish_reason = finish_reason

        self.choices = [Choice(Delta(content), finish_reason)]


@pytest.fixture
def make_streaming_chunk():
    """Factory fixture for creating streaming chunk objects.

    Returns a function that creates mock streaming chunks
    for testing reconstruct_full_response_from_chunks.

    Usage:
        chunk = make_streaming_chunk(content="Hello", id="msg-123", model="gpt-4")
    """

    def _make(
        content: str | None = None,
        id: str = "chatcmpl-123",
        model: str = DEFAULT_TEST_MODEL,
        finish_reason: str | None = None,
    ):
        """Create a streaming chunk object."""
        return _StreamingChunk(content=content, id=id, model=model, finish_reason=finish_reason)

    return _make
