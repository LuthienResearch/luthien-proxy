"""Tests for upstream header injection."""

from __future__ import annotations

import json

import pytest

from luthien_proxy.pipeline.upstream_headers import (
    _expand_template,
    _load_header_templates,
    expand_upstream_headers,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the lru_cache between tests."""
    _load_header_templates.cache_clear()
    yield
    _load_header_templates.cache_clear()


class TestLoadHeaderTemplates:
    """Tests for parsing UPSTREAM_HEADERS env var."""

    def test_returns_empty_when_unset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("UPSTREAM_HEADERS", raising=False)
        assert _load_header_templates() == {}

    def test_returns_empty_for_empty_string(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", "")
        assert _load_header_templates() == {}

    def test_parses_valid_json(self, monkeypatch: pytest.MonkeyPatch):
        headers = {"Helicone-Auth": "Bearer key123", "X-Custom": "value"}
        monkeypatch.setenv("UPSTREAM_HEADERS", json.dumps(headers))
        assert _load_header_templates() == headers

    def test_returns_empty_for_invalid_json(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", "not json")
        assert _load_header_templates() == {}

    def test_returns_empty_for_non_object_json(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", '["not", "an", "object"]')
        assert _load_header_templates() == {}

    def test_skips_non_string_values(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", '{"good": "value", "bad": 123}')
        result = _load_header_templates()
        assert result == {"good": "value"}
        assert "bad" not in result


class TestExpandTemplate:
    """Tests for template variable expansion."""

    def test_expands_session_id(self):
        assert _expand_template("sess-${session_id}", "abc-123", "/v1/messages") == "sess-abc-123"

    def test_expands_none_session_id_to_empty(self):
        assert _expand_template("${session_id}", None, "/v1/messages") == ""

    def test_expands_request_path(self):
        assert _expand_template("${request_path}", None, "/v1/messages") == "/v1/messages"

    def test_expands_env_var(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_SECRET", "s3cret")
        assert _expand_template("Bearer ${env.MY_SECRET}", None, "/") == "Bearer s3cret"

    def test_missing_env_var_expands_to_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        assert _expand_template("${env.NONEXISTENT_VAR}", None, "/") == ""

    def test_unknown_variable_left_unexpanded(self):
        assert _expand_template("${unknown_var}", None, "/") == "${unknown_var}"

    def test_multiple_variables_in_one_template(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("USER", "sami")
        result = _expand_template("${env.USER}:${session_id}", "sess-1", "/v1/messages")
        assert result == "sami:sess-1"

    def test_no_variables_returns_literal(self):
        assert _expand_template("plain value", None, "/") == "plain value"


class TestExpandUpstreamHeaders:
    """Tests for the full expansion pipeline."""

    def test_returns_none_when_no_config(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("UPSTREAM_HEADERS", raising=False)
        assert expand_upstream_headers("sess-1", "/v1/messages") is None

    def test_expands_all_headers(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HELICONE_API_KEY", "sk-hel-test")
        monkeypatch.setenv(
            "UPSTREAM_HEADERS",
            json.dumps(
                {
                    "Helicone-Auth": "Bearer ${env.HELICONE_API_KEY}",
                    "Helicone-Session-Id": "${session_id}",
                    "Helicone-Session-Path": "${request_path}",
                }
            ),
        )
        result = expand_upstream_headers("abc-123-uuid", "/v1/messages")
        assert result is not None
        assert result["Helicone-Auth"] == "Bearer sk-hel-test"
        assert result["Helicone-Session-Id"] == "abc-123-uuid"
        assert result["Helicone-Session-Path"] == "/v1/messages"

    def test_skips_headers_that_expand_to_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            "UPSTREAM_HEADERS",
            json.dumps(
                {
                    "Helicone-Session-Id": "${session_id}",
                    "Helicone-Auth": "Bearer static-key",
                }
            ),
        )
        # session_id is None → Helicone-Session-Id expands to "" → skipped
        result = expand_upstream_headers(None, "/v1/messages")
        assert result is not None
        assert "Helicone-Session-Id" not in result
        assert result["Helicone-Auth"] == "Bearer static-key"

    def test_returns_none_when_all_expand_to_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            "UPSTREAM_HEADERS",
            json.dumps({"Helicone-Session-Id": "${session_id}"}),
        )
        assert expand_upstream_headers(None, "/v1/messages") is None

    def test_helicone_full_config(self, monkeypatch: pytest.MonkeyPatch):
        """Integration-style test matching the real Helicone use case."""
        monkeypatch.setenv("HELICONE_API_KEY", "sk-helicone-prod")
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_prod_key")
        monkeypatch.setenv("USER", "sami@trajectory.dev")
        monkeypatch.setenv(
            "UPSTREAM_HEADERS",
            json.dumps(
                {
                    "Helicone-Auth": "Bearer ${env.HELICONE_API_KEY}",
                    "Helicone-Session-Id": "${session_id}",
                    "Helicone-Session-Name": "Claude Code",
                    "Helicone-Session-Path": "${request_path}",
                    "Helicone-User-Id": "${env.USER}",
                    "Helicone-Posthog-Key": "${env.POSTHOG_API_KEY}",
                    "Helicone-Posthog-Host": "https://us.i.posthog.com",
                    "Helicone-Property-SessionId": "${session_id}",
                    "Helicone-Property-UserId": "${env.USER}",
                }
            ),
        )
        result = expand_upstream_headers("9f3a-b2c1-session-uuid", "/v1/messages")
        assert result is not None
        assert result == {
            "Helicone-Auth": "Bearer sk-helicone-prod",
            "Helicone-Session-Id": "9f3a-b2c1-session-uuid",
            "Helicone-Session-Name": "Claude Code",
            "Helicone-Session-Path": "/v1/messages",
            "Helicone-User-Id": "sami@trajectory.dev",
            "Helicone-Posthog-Key": "phc_prod_key",
            "Helicone-Posthog-Host": "https://us.i.posthog.com",
            "Helicone-Property-SessionId": "9f3a-b2c1-session-uuid",
            "Helicone-Property-UserId": "sami@trajectory.dev",
        }
