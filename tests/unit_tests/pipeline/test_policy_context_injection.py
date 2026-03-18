"""Tests for policy awareness context injection."""

from __future__ import annotations

from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import AnthropicRequest
from luthien_proxy.pipeline.policy_context_injection import (
    POLICY_AWARENESS_PREFIX,
    build_awareness_message,
    get_policy_names,
    inject_policy_awareness_anthropic,
    inject_policy_awareness_openai,
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


def _make_openai_request(**kwargs: object) -> Request:
    defaults = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
    defaults.update(kwargs)
    return Request(**defaults)


def _make_anthropic_request(**overrides: object) -> AnthropicRequest:
    base: AnthropicRequest = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 1024,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# =========================================================================
# get_policy_names
# =========================================================================


class TestGetPolicyNames:
    def test_noop_returns_empty(self) -> None:
        assert get_policy_names(NoOpPolicy()) == []

    def test_simple_policy_returns_name(self) -> None:
        assert get_policy_names(FakePolicy("AllCaps")) == ["AllCaps"]

    def test_multi_policy_returns_leaf_names(self) -> None:
        multi = FakeMultiPolicy([FakePolicy("AllCaps"), FakePolicy("StringReplace")])
        assert get_policy_names(multi) == ["AllCaps", "StringReplace"]

    def test_multi_policy_filters_noop(self) -> None:
        multi = FakeMultiPolicy([NoOpPolicy(), FakePolicy("AllCaps")])
        assert get_policy_names(multi) == ["AllCaps"]

    def test_multi_policy_all_noop_returns_empty(self) -> None:
        multi = FakeMultiPolicy([NoOpPolicy(), NoOpPolicy()])
        assert get_policy_names(multi) == []

    def test_nested_multi_policy(self) -> None:
        inner = FakeMultiPolicy([FakePolicy("A"), FakePolicy("B")])
        outer = FakeMultiPolicy([inner, FakePolicy("C")])
        assert get_policy_names(outer) == ["A", "B", "C"]


# =========================================================================
# build_awareness_message
# =========================================================================


class TestBuildAwarenessMessage:
    def test_includes_prefix(self) -> None:
        msg = build_awareness_message(["AllCaps"])
        assert msg.startswith(POLICY_AWARENESS_PREFIX)

    def test_includes_policy_names(self) -> None:
        msg = build_awareness_message(["AllCaps", "StringReplace"])
        assert "AllCaps" in msg
        assert "StringReplace" in msg


# =========================================================================
# inject_policy_awareness_openai
# =========================================================================


class TestInjectOpenAI:
    def test_noop_policy_no_injection(self) -> None:
        request = _make_openai_request()
        result = inject_policy_awareness_openai(request, NoOpPolicy())
        assert result.messages == request.messages

    def test_injects_new_system_message(self) -> None:
        request = _make_openai_request()
        result = inject_policy_awareness_openai(request, FakePolicy("AllCaps"))
        assert result.messages[0]["role"] == "system"
        assert "AllCaps" in result.messages[0]["content"]

    def test_appends_to_existing_system_message(self) -> None:
        request = _make_openai_request(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
            ]
        )
        result = inject_policy_awareness_openai(request, FakePolicy("AllCaps"))
        system_content = result.messages[0]["content"]
        assert "You are a helpful assistant." in system_content
        assert "AllCaps" in system_content

    def test_appends_to_list_system_content(self) -> None:
        request = _make_openai_request(
            messages=[
                {"role": "system", "content": [{"type": "text", "text": "Be helpful."}]},
                {"role": "user", "content": "Hello"},
            ]
        )
        result = inject_policy_awareness_openai(request, FakePolicy("AllCaps"))
        system_content = result.messages[0]["content"]
        assert isinstance(system_content, list)
        assert len(system_content) == 2
        assert "AllCaps" in system_content[1]["text"]

    def test_does_not_mutate_original_request(self) -> None:
        request = _make_openai_request()
        original_messages = list(request.messages)
        inject_policy_awareness_openai(request, FakePolicy("AllCaps"))
        assert request.messages == original_messages

    def test_multi_policy_lists_all_names(self) -> None:
        multi = FakeMultiPolicy([FakePolicy("A"), FakePolicy("B")])
        request = _make_openai_request()
        result = inject_policy_awareness_openai(request, multi)
        content = result.messages[0]["content"]
        assert "A" in content
        assert "B" in content


# =========================================================================
# inject_policy_awareness_anthropic
# =========================================================================


class TestInjectAnthropic:
    def test_noop_policy_no_injection(self) -> None:
        request = _make_anthropic_request()
        result = inject_policy_awareness_anthropic(request, NoOpPolicy())
        assert "system" not in result

    def test_injects_system_string(self) -> None:
        request = _make_anthropic_request()
        result = inject_policy_awareness_anthropic(request, FakePolicy("AllCaps"))
        assert isinstance(result["system"], str)
        assert "AllCaps" in result["system"]

    def test_appends_to_existing_system_string(self) -> None:
        request = _make_anthropic_request(system="Be helpful.")
        result = inject_policy_awareness_anthropic(request, FakePolicy("AllCaps"))
        assert "Be helpful." in result["system"]
        assert "AllCaps" in result["system"]

    def test_appends_to_existing_system_blocks(self) -> None:
        request = _make_anthropic_request(system=[{"type": "text", "text": "Be helpful."}])
        result = inject_policy_awareness_anthropic(request, FakePolicy("AllCaps"))
        system = result["system"]
        assert isinstance(system, list)
        assert len(system) == 2
        assert "AllCaps" in system[1]["text"]

    def test_does_not_mutate_original_request(self) -> None:
        request = _make_anthropic_request(system=[{"type": "text", "text": "Be helpful."}])
        original_system = list(request["system"])
        inject_policy_awareness_anthropic(request, FakePolicy("AllCaps"))
        assert request["system"] == original_system
