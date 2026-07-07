"""Tests for actionable error advice (pipeline/error_advice.py)."""

import pytest

from luthien_proxy.pipeline.error_advice import (
    CONNECTION_ERROR_ADVICE,
    CREDENTIAL_ERROR_ADVICE,
    INTERNAL_ERROR_ADVICE,
    SUGGESTION_PREFIX,
    append_advice,
    get_error_advice,
)


class TestGetErrorAdvice:
    """Advice lookup for upstream status codes and message patterns."""

    @pytest.mark.parametrize(
        "status_code,message,expected_fragment",
        [
            (400, "banana_mode: Extra inputs are not permitted", "does not recognize"),
            (400, "max_tokens: 999999 > 64000, which is the maximum", "max_tokens"),
            (400, "Your credit balance is too low to access the API", "credits"),
            (400, "messages: roles must alternate", "rejected this request as invalid"),
            (401, "invalid x-api-key", "credentials"),
            (403, "forbidden", "access"),
            (404, "model: claude-nonexistent not found", "model name"),
            (404, "not found", "resource was not found"),
            (413, "payload too large", "too large"),
            (422, "invalid body", "rejected this request as invalid"),
            (429, "rate limit exceeded", "retry with backoff"),
            (500, "internal server error", "transient"),
            (529, "overloaded", "overloaded"),
        ],
    )
    def test_known_errors_get_specific_advice(self, status_code, message, expected_fragment):
        advice = get_error_advice(status_code, message)
        assert expected_fragment in advice

    def test_unknown_status_gets_generic_advice(self):
        advice = get_error_advice(418, "I'm a teapot")
        assert advice
        assert "Retry" in advice

    def test_none_status_gets_generic_advice(self):
        advice = get_error_advice(None, "mystery error")
        assert advice


class TestAppendAdvice:
    """Advice is appended without destroying the raw upstream message."""

    def test_preserves_raw_message(self):
        raw = "banana_mode: Extra inputs are not permitted"
        combined = append_advice(raw, "Remove the field.")
        assert combined.startswith(raw)
        assert f"{SUGGESTION_PREFIX} Remove the field." in combined

    def test_none_advice_returns_message_unchanged(self):
        assert append_advice("raw error", None) == "raw error"

    def test_empty_advice_returns_message_unchanged(self):
        assert append_advice("raw error", "") == "raw error"

    def test_does_not_double_append(self):
        once = append_advice("raw error", "Do the thing.")
        twice = append_advice(once, "Do the other thing.")
        assert twice == once

    def test_module_advice_constants_are_actionable(self):
        for advice in (CONNECTION_ERROR_ADVICE, CREDENTIAL_ERROR_ADVICE, INTERNAL_ERROR_ADVICE):
            assert "Luthien proxy" in advice
