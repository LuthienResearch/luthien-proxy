"""Tests for policy awareness context injection."""

from __future__ import annotations

from luthien_proxy.llm.types.anthropic import AnthropicRequest
from luthien_proxy.pipeline.policy_context_injection import (
    _CONTEXT_OPEN,
    build_awareness_message,
    inject_policy_awareness_anthropic,
)
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_core.base_policy import BasePolicy

# =========================================================================
# Test helpers
# =========================================================================


class FakePolicy(BasePolicy):
    """A fake policy with a custom name."""

    def __init__(self, name: str = "FakePolicy") -> None:
        self._name = name

    @property
    def short_policy_name(self) -> str:
        return self._name


class FakeMultiPolicy(BasePolicy):
    """A fake multi-policy with sub-policies."""

    def __init__(self, sub_policies: list[BasePolicy]) -> None:
        self._sub_policies = tuple(sub_policies)

    @property
    def short_policy_name(self) -> str:
        names = [p.short_policy_name for p in self._sub_policies]
        return f"Multi({', '.join(names)})"

    def active_policy_names(self) -> list[str]:
        names: list[str] = []
        for p in self._sub_policies:
            names.extend(p.active_policy_names())
        return names


def _make_anthropic_request(**overrides: object) -> AnthropicRequest:
    base: AnthropicRequest = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 1024,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# =========================================================================
# active_policy_names
# =========================================================================


class TestActivePolicyNames:
    def test_noop_returns_empty(self) -> None:
        assert NoOpPolicy().active_policy_names() == []

    def test_simple_policy_returns_name(self) -> None:
        assert FakePolicy("AllCaps").active_policy_names() == ["AllCaps"]

    def test_multi_policy_returns_leaf_names(self) -> None:
        multi = FakeMultiPolicy([FakePolicy("AllCaps"), FakePolicy("StringReplace")])
        assert multi.active_policy_names() == ["AllCaps", "StringReplace"]

    def test_multi_policy_filters_noop(self) -> None:
        multi = FakeMultiPolicy([NoOpPolicy(), FakePolicy("AllCaps")])
        assert multi.active_policy_names() == ["AllCaps"]

    def test_multi_policy_all_noop_returns_empty(self) -> None:
        multi = FakeMultiPolicy([NoOpPolicy(), NoOpPolicy()])
        assert multi.active_policy_names() == []

    def test_nested_multi_policy(self) -> None:
        inner = FakeMultiPolicy([FakePolicy("A"), FakePolicy("B")])
        outer = FakeMultiPolicy([inner, FakePolicy("C")])
        assert outer.active_policy_names() == ["A", "B", "C"]


# =========================================================================
# build_awareness_message
# =========================================================================


class TestBuildAwarenessMessage:
    def test_includes_context_tag(self) -> None:
        msg = build_awareness_message(["AllCaps"])
        assert _CONTEXT_OPEN in msg

    def test_includes_policy_names(self) -> None:
        msg = build_awareness_message(["AllCaps", "StringReplace"])
        assert "AllCaps" in msg
        assert "StringReplace" in msg


# =========================================================================
# inject_policy_awareness_anthropic
# =========================================================================


class TestInjectAnthropic:
    def test_empty_names_no_injection(self) -> None:
        request = _make_anthropic_request()
        result = inject_policy_awareness_anthropic(request, [])
        assert result["messages"] == request["messages"]

    def test_injects_into_first_user_message(self) -> None:
        request = _make_anthropic_request()
        result = inject_policy_awareness_anthropic(request, ["AllCaps"])
        assert "AllCaps" in result["messages"][0]["content"]
        assert _CONTEXT_OPEN in result["messages"][0]["content"]

    def test_prepends_to_list_user_content(self) -> None:
        request = _make_anthropic_request(messages=[{"role": "user", "content": [{"type": "text", "text": "Hello"}]}])
        result = inject_policy_awareness_anthropic(request, ["AllCaps"])
        content = result["messages"][0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert "AllCaps" in content[0]["text"]

    def test_skips_if_already_injected(self) -> None:
        request = _make_anthropic_request()
        first = inject_policy_awareness_anthropic(request, ["AllCaps"])
        second = inject_policy_awareness_anthropic(first, ["AllCaps"])
        assert first["messages"] == second["messages"]

    def test_does_not_mutate_original_request(self) -> None:
        request = _make_anthropic_request()
        original_messages = list(request["messages"])
        inject_policy_awareness_anthropic(request, ["AllCaps"])
        assert request["messages"] == original_messages

    def test_system_field_unchanged(self) -> None:
        """Injection goes into messages, not the system field."""
        request = _make_anthropic_request(system="Be helpful.")
        result = inject_policy_awareness_anthropic(request, ["AllCaps"])
        assert result["system"] == "Be helpful."
        assert "AllCaps" in result["messages"][0]["content"]
