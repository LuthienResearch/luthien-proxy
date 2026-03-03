# ABOUTME: Shared test fixtures for V2 tests
# ABOUTME: Provides properly structured ModelResponse objects to avoid Pydantic warnings

"""Shared fixtures for V2 tests."""

import importlib.util
import socket
import warnings
from pathlib import Path

import pytest
from litellm.types.utils import Choices, Message, ModelResponse
from tests.unit_tests.helpers.litellm_test_utils import (
    make_streaming_chunk as _make_streaming_chunk,
)

# Re-export DEFAULT_TEST_MODEL from the root tests/conftest.py so that
# `from conftest import DEFAULT_TEST_MODEL` resolves correctly even when
# this local conftest shadows the root one.
_root_conftest_path = Path(__file__).resolve().parent.parent / "conftest.py"
_root_conftest_spec = importlib.util.spec_from_file_location("_root_conftest", _root_conftest_path)
_root_conftest = importlib.util.module_from_spec(_root_conftest_spec)  # type: ignore[arg-type]
_root_conftest_spec.loader.exec_module(_root_conftest)  # type: ignore[union-attr]
DEFAULT_TEST_MODEL: str = _root_conftest.DEFAULT_TEST_MODEL

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


@pytest.fixture(autouse=True)
def suppress_litellm_pydantic_warnings():
    """Suppress Pydantic serialization warnings from LiteLLM's Union types.

    LiteLLM uses Union[Choices, StreamingChoices] which causes Pydantic to emit
    warnings when serializing, even though the serialization works correctly.
    These warnings are noise and don't indicate actual problems.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            message=".*Pydantic serializer warnings.*",
        )
        yield


@pytest.fixture
def make_model_response():
    """Factory fixture for creating complete ModelResponse objects.

    Returns a function that creates fully-formed ModelResponse objects
    with all required fields to avoid Pydantic serialization warnings.

    Usage:
        response = make_model_response(content="Hello world")
        response = make_model_response(content="", model="gpt-3.5-turbo")
    """

    def _make(content: str, model: str = "gpt-4", id: str = "test-response-id") -> ModelResponse:
        """Create a complete non-streaming ModelResponse."""
        return ModelResponse(
            id=id,
            created=1234567890,
            model=model,
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content=content),
                    finish_reason="stop",
                )
            ],
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        )

    return _make


@pytest.fixture
def make_streaming_chunk():
    """Factory fixture for creating streaming chunk ModelResponse objects.

    Returns a function that creates fully-formed, normalized streaming chunks
    as litellm_client.stream() would return them.

    Usage:
        chunk = make_streaming_chunk(content="Hello ")
        chunk = make_streaming_chunk(content="world", finish_reason="stop")
    """
    return _make_streaming_chunk
