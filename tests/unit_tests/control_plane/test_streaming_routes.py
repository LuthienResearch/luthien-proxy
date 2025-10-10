from __future__ import annotations

import pytest
from litellm.types.utils import ModelResponseStream

from luthien_proxy.control_plane.streaming_routes import (
    StreamProtocolError,
    _canonicalize_chunk,
)


def _sample_chunk(content: str = "hello") -> dict[str, object]:
    """Return a minimal OpenAI-style chunk dict."""
    model = ModelResponseStream.model_validate(
        {
            "id": "chunk-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-unified",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": None,
                }
            ],
        }
    )
    return model.model_dump()


def test_canonicalize_chunk_returns_normalized_payload() -> None:
    payload = _sample_chunk("hi there")

    normalized = _canonicalize_chunk("stream-1", "upstream", payload)

    assert normalized["id"] == "chunk-1"
    assert normalized["choices"][0]["delta"]["content"] == "hi there"
    assert normalized is not payload


def test_canonicalize_chunk_rejects_invalid_payload() -> None:
    with pytest.raises(StreamProtocolError):
        _canonicalize_chunk("stream-1", "upstream", {"foo": "bar"})
