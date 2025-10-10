from __future__ import annotations

from luthien_proxy.control_plane.conversation.utils import json_safe


def _make_nested_dict(depth: int) -> dict:
    root: dict = {}
    current = root
    for _ in range(depth):
        next_layer: dict = {}
        current["next"] = next_layer
        current = next_layer
    current["value"] = "end"
    return root


def test_json_safe_enforces_depth_limit() -> None:
    nested = _make_nested_dict(5)

    safe = json_safe(nested, max_depth=2)

    assert isinstance(safe, dict)
    level_1 = safe["next"]
    level_2 = level_1["next"]
    assert isinstance(level_2, dict)
    assert level_2["next"] == "<max-depth-exceeded>"


def test_json_safe_detects_cycles() -> None:
    recursive: list = []
    recursive.append(recursive)

    safe = json_safe(recursive)

    assert safe == ["<recursion>"]
