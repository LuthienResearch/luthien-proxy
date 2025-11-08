# ABOUTME: Shared test fixtures for V2 tests
# ABOUTME: Provides properly structured ModelResponse objects to avoid Pydantic warnings

"""Shared fixtures for V2 tests."""

import warnings

import pytest
from litellm.types.utils import Choices, Delta, Message, ModelResponse, StreamingChoices


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
