# ABOUTME: Shared test fixtures for V2 tests
# ABOUTME: Provides properly structured ModelResponse objects to avoid Pydantic warnings

"""Shared fixtures for V2 tests."""

import warnings
from contextlib import asynccontextmanager
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from litellm.types.utils import Choices, Delta, Message, ModelResponse, StreamingChoices


def create_test_lifespan(
    control_plane: Any,
    db_pool: Optional[Any] = None,
    redis_client: Optional[Any] = None,
    event_publisher: Optional[Any] = None,
    api_key: str = "test-api-key",
):
    """Create a test lifespan context manager with mocked dependencies.

    This factory creates a lifespan that mimics the production lifespan in
    src/luthien_proxy/v2/main.py but with injected test doubles instead of
    real database/redis connections.

    Args:
        control_plane: Mock or fake control plane instance
        db_pool: Optional mock database pool (None to disable DB)
        redis_client: Optional mock Redis client (None to disable Redis)
        event_publisher: Optional mock event publisher (None to disable events)
        api_key: API key to use for authentication (default: "test-api-key")

    Returns:
        Async context manager suitable for FastAPI(lifespan=...)

    Example:
        >>> control_plane = FakeControlPlane()
        >>> lifespan = create_test_lifespan(control_plane=control_plane)
        >>> app = FastAPI(lifespan=lifespan)
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Test lifespan that injects mocked dependencies into app.state."""
        # Store test dependencies in app state (same as production)
        # Use v2_ prefix for consistency with both standalone and mounted apps
        app.state.v2_db_pool = db_pool
        app.state.v2_redis_client = redis_client
        app.state.v2_event_publisher = event_publisher
        app.state.v2_control_plane = control_plane
        app.state.v2_api_key = api_key

        yield

        # Cleanup: For mocks, usually no cleanup needed
        # But this mirrors production structure for consistency

    return lifespan


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

    Returns a function that creates fully-formed streaming chunks
    with all required fields to avoid Pydantic serialization warnings.

    Usage:
        chunk = make_streaming_chunk(content="Hello ")
        chunk = make_streaming_chunk(content="world", finish_reason="stop")
    """

    def _make(
        content: str | None,
        model: str = "gpt-4",
        id: str = "test-chunk-id",
        finish_reason: str | None = None,
    ) -> ModelResponse:
        """Create a complete streaming chunk."""
        return ModelResponse(
            id=id,
            created=1234567890,
            model=model,
            object="chat.completion.chunk",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(role="assistant", content=content),
                    finish_reason=finish_reason,
                )
            ],
        )

    return _make
