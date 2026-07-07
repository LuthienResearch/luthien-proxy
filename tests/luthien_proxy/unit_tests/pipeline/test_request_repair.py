"""Tests for fixable-400 request repair (pipeline/request_repair.py)."""

import copy

from luthien_proxy.llm.types.anthropic import AnthropicRequest
from luthien_proxy.pipeline.request_repair import attempt_request_fix


def _base_request() -> AnthropicRequest:
    return {
        "model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 1024,
    }


class TestAttemptRequestFix:
    """Repair of the 'Extra inputs are not permitted' 400 pattern."""

    def test_strips_top_level_extra_field(self):
        request = _base_request()
        request["banana_mode"] = True  # type: ignore[typeddict-unknown-key]

        fix = attempt_request_fix(request, "banana_mode: Extra inputs are not permitted")

        assert fix is not None
        assert fix.removed_field == "banana_mode"
        assert "banana_mode" not in fix.request
        assert fix.request["model"] == request["model"]
        assert "banana_mode" in fix.description

    def test_strips_nested_field_through_list_index(self):
        request = _base_request()
        request["messages"] = [{"role": "user", "content": "Hi", "bogus": 1}]

        fix = attempt_request_fix(request, "messages.0.bogus: Extra inputs are not permitted")

        assert fix is not None
        assert fix.removed_field == "messages.0.bogus"
        assert "bogus" not in fix.request["messages"][0]
        assert fix.request["messages"][0]["role"] == "user"

    def test_original_request_is_not_mutated(self):
        request = _base_request()
        request["banana_mode"] = True  # type: ignore[typeddict-unknown-key]
        snapshot = copy.deepcopy(dict(request))

        attempt_request_fix(request, "banana_mode: Extra inputs are not permitted")

        assert dict(request) == snapshot

    def test_protected_field_is_not_removed(self):
        request = _base_request()

        fix = attempt_request_fix(request, "max_tokens: Extra inputs are not permitted")

        assert fix is None

    def test_non_matching_message_returns_none(self):
        request = _base_request()

        fix = attempt_request_fix(request, "messages: roles must alternate")

        assert fix is None

    def test_field_absent_from_payload_returns_none(self):
        request = _base_request()

        fix = attempt_request_fix(request, "ghost_field: Extra inputs are not permitted")

        assert fix is None

    def test_unresolvable_nested_path_returns_none(self):
        request = _base_request()

        fix = attempt_request_fix(request, "messages.9.bogus: Extra inputs are not permitted")

        assert fix is None

    def test_field_path_embedded_in_longer_message(self):
        request = _base_request()
        request["banana_mode"] = True  # type: ignore[typeddict-unknown-key]

        fix = attempt_request_fix(
            request,
            'Error code: 400 - {"error": {"message": "banana_mode: Extra inputs are not permitted"}}',
        )

        assert fix is not None
        assert fix.removed_field == "banana_mode"
