from __future__ import annotations

from config.litellm_callback import LuthienCallback
from config.unified_callback import UnifiedCallback, _is_sse_chunk


def _make_deep_mapping(depth: int) -> dict:
    root: dict = {}
    node = root
    for _ in range(depth):
        child: dict = {}
        node["next"] = child
        node = child
    node["value"] = "end"
    return root


def _contains_value(structure: object, target: object, limit: int = 128) -> bool:
    if limit <= 0:
        return False
    if structure == target:
        return True
    if isinstance(structure, dict):
        return any(_contains_value(v, target, limit - 1) for v in structure.values())
    if isinstance(structure, list):
        return any(_contains_value(item, target, limit - 1) for item in structure)
    return False


def test_luthien_callback_json_safe_depth_cap() -> None:
    callback = LuthienCallback()
    nested = _make_deep_mapping(40)

    safe = callback._json_safe(nested)

    assert _contains_value(safe, "<max-depth-exceeded>")


def test_luthien_callback_json_safe_cycle() -> None:
    callback = LuthienCallback()
    cyc: list = []
    cyc.append(cyc)

    safe = callback._json_safe(cyc)

    assert safe == ["<recursion>"]


def test_unified_callback_json_safe_depth_cap() -> None:
    callback = UnifiedCallback()
    nested = _make_deep_mapping(40)

    safe = callback._json_safe(nested)

    assert _contains_value(safe, "<max-depth-exceeded>")


def test_unified_callback_json_safe_cycle() -> None:
    callback = UnifiedCallback()
    cyc: list = []
    cyc.append(cyc)

    safe = callback._json_safe(cyc)

    assert safe == ["<recursion>"]


def test_is_sse_chunk_detects_anthropic_events() -> None:
    """Verify that SSE-formatted chunks are correctly identified."""
    # Anthropic SSE event (bytes)
    anthropic_bytes = b'event: message_start\ndata: {"type":"message_start"}\n\n'
    assert _is_sse_chunk(anthropic_bytes) is True

    # Anthropic SSE event (string)
    anthropic_str = 'event: content_block_delta\ndata: {"delta":{"text":"hello"}}\n\n'
    assert _is_sse_chunk(anthropic_str) is True

    # SSE data-only event
    data_only = 'data: {"type":"message_stop"}\n\n'
    assert _is_sse_chunk(data_only) is True


def test_is_sse_chunk_rejects_openai_chunks() -> None:
    """Verify that OpenAI-style dict chunks are not identified as SSE."""
    # OpenAI chunk (dict)
    openai_chunk = {
        "id": "chatcmpl-123",
        "object": "chat.completion.chunk",
        "created": 1234567890,
        "model": "gpt-4",
        "choices": [{"delta": {"content": "hello"}, "finish_reason": None}],
    }
    assert _is_sse_chunk(openai_chunk) is False

    # Random string that doesn't look like SSE
    random_string = "this is just plain text"
    assert _is_sse_chunk(random_string) is False

    # Empty string
    assert _is_sse_chunk("") is False

    # None
    assert _is_sse_chunk(None) is False
