from __future__ import annotations

from config.litellm_callback import LuthienCallback
from config.unified_callback import UnifiedCallback


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
