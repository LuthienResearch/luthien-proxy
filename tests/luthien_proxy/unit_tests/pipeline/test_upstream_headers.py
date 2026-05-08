"""Tests for upstream header injection."""

from __future__ import annotations

import json
import logging

import pytest

from luthien_proxy.pipeline.upstream_headers import (
    _expand_template,
    _load_header_templates,
    expand_upstream_headers,
    merge_forwarded_headers,
    validate_upstream_headers_at_startup,
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

    def test_raises_on_invalid_json(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", "not json")
        with pytest.raises(json.JSONDecodeError):
            _load_header_templates()

    def test_raises_on_non_object_root(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", '["not", "an", "object"]')
        with pytest.raises(ValueError, match="must be a JSON object"):
            _load_header_templates()

    def test_raises_on_non_string_value(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", '{"good": 123}')
        with pytest.raises(ValueError, match="must be a string"):
            _load_header_templates()

    def test_raises_on_invalid_header_name(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", '{"bad name with spaces": "value"}')
        with pytest.raises(ValueError, match="not a valid RFC 7230 token"):
            _load_header_templates()

    @pytest.mark.parametrize(
        "bad_ref",
        [
            "${env.}",  # empty
            "${env.WITH SPACE}",  # space
            "${env.WITH-DASH}",  # dash
            "${env.1LEADING_DIGIT}",
        ],
    )
    def test_raises_on_invalid_env_var_reference(self, monkeypatch: pytest.MonkeyPatch, bad_ref: str):
        monkeypatch.setenv("UPSTREAM_HEADERS", json.dumps({"X-Custom": f"prefix {bad_ref} suffix"}))
        with pytest.raises(ValueError, match="invalid env var reference"):
            _load_header_templates()

    def test_accepts_valid_env_var_references(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", json.dumps({"X-A": "${env.FOO}", "X-B": "${env._UNDER}"}))
        # Does not raise.
        _load_header_templates()

    def test_drops_hop_by_hop_headers_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv(
            "UPSTREAM_HEADERS",
            json.dumps(
                {
                    "Connection": "close",
                    "Transfer-Encoding": "chunked",
                    "Content-Length": "0",
                    "Keep-Alive": "timeout=5",
                    "Proxy-Authenticate": "Basic",
                    "Proxy-Authorization": "Basic abc",
                    "Proxy-Connection": "close",
                    "TE": "trailers",
                    "Trailer": "Expires",
                    "Trailers": "Expires",
                    "Upgrade": "h2c",
                    "X-Custom": "kept",
                }
            ),
        )
        with caplog.at_level(logging.WARNING, logger="luthien_proxy.pipeline.upstream_headers"):
            result = _load_header_templates()
        assert result == {"X-Custom": "kept"}
        assert "Connection" in caplog.text
        assert "Transfer-Encoding" in caplog.text
        assert "Upgrade" in caplog.text
        assert "Proxy-Authorization" in caplog.text

    def test_reserved_check_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", json.dumps({"CONTENT-LENGTH": "0", "X-Custom": "kept"}))
        assert _load_header_templates() == {"X-Custom": "kept"}

    def test_authorization_header_is_allowed(self, monkeypatch: pytest.MonkeyPatch):
        # Operator may legitimately want to override Authorization on the way upstream.
        monkeypatch.setenv("UPSTREAM_HEADERS", json.dumps({"Authorization": "Bearer override"}))
        assert _load_header_templates() == {"Authorization": "Bearer override"}

    def test_raises_on_intra_config_case_insensitive_duplicate(self, monkeypatch: pytest.MonkeyPatch):
        # Two casings of the same logical header would otherwise ship as two header lines.
        monkeypatch.setenv("UPSTREAM_HEADERS", json.dumps({"Helicone-Auth": "a", "HELICONE-AUTH": "b"}))
        with pytest.raises(ValueError, match="duplicate header"):
            _load_header_templates()

    def test_audit_log_lists_referenced_env_vars(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("HELICONE_API_KEY", "x")
        monkeypatch.setenv("USER", "y")
        monkeypatch.setenv(
            "UPSTREAM_HEADERS",
            json.dumps(
                {
                    "Helicone-Auth": "Bearer ${env.HELICONE_API_KEY}",
                    "Helicone-User": "${env.USER}",
                }
            ),
        )
        with caplog.at_level(logging.INFO, logger="luthien_proxy.pipeline.upstream_headers"):
            _load_header_templates()
        assert "HELICONE_API_KEY" in caplog.text
        assert "USER" in caplog.text

    def test_warns_on_unset_referenced_env_vars(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.delenv("HELICONE_API_KEY", raising=False)
        monkeypatch.delenv("MISSING_THING", raising=False)
        monkeypatch.setenv("USER", "present")
        monkeypatch.setenv(
            "UPSTREAM_HEADERS",
            json.dumps(
                {
                    "Helicone-Auth": "Bearer ${env.HELICONE_API_KEY}",
                    "Helicone-User": "${env.USER}",
                    "X-Other": "${env.MISSING_THING}",
                }
            ),
        )
        with caplog.at_level(logging.WARNING, logger="luthien_proxy.pipeline.upstream_headers"):
            _load_header_templates()
        assert "unset" in caplog.text.lower()
        assert "HELICONE_API_KEY" in caplog.text
        assert "MISSING_THING" in caplog.text
        # USER is set, must not appear in the unset warning line.
        # (It will appear in the "referencing" info line, which is fine.)
        warning_lines = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("HELICONE_API_KEY" in r.getMessage() for r in warning_lines)
        assert not any("USER" in r.getMessage() and "unset" in r.getMessage() for r in warning_lines)

    def test_unknown_template_var_warns_at_load_time(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("UPSTREAM_HEADERS", json.dumps({"X-Custom": "${not_a_real_var}"}))
        with caplog.at_level(logging.WARNING, logger="luthien_proxy.pipeline.upstream_headers"):
            _load_header_templates()
        assert "not_a_real_var" in caplog.text

    def test_known_vars_do_not_warn(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        monkeypatch.setenv(
            "UPSTREAM_HEADERS",
            json.dumps({"X-Sess": "${session_id}", "X-Path": "${request_path}"}),
        )
        with caplog.at_level(logging.WARNING, logger="luthien_proxy.pipeline.upstream_headers"):
            _load_header_templates()
        assert "unknown template variable" not in caplog.text


class TestValidateAtStartup:
    """Tests for the startup validator."""

    def test_passes_when_unset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("UPSTREAM_HEADERS", raising=False)
        validate_upstream_headers_at_startup()  # does not raise

    def test_passes_for_valid_config(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", json.dumps({"X-Custom": "value"}))
        validate_upstream_headers_at_startup()  # does not raise

    def test_raises_for_invalid_json(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", "not json")
        with pytest.raises(json.JSONDecodeError):
            validate_upstream_headers_at_startup()

    def test_raises_for_invalid_header_name(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("UPSTREAM_HEADERS", json.dumps({"bad name": "value"}))
        with pytest.raises(ValueError):
            validate_upstream_headers_at_startup()


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

    def test_strips_crlf_from_literal_value(self):
        assert _expand_template("value\r\nX-Injected: evil", None, "/") == "valueX-Injected: evil"

    def test_strips_crlf_from_env_var_expansion(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("EVIL_VAR", "legit\r\nX-Injected: evil")
        assert _expand_template("${env.EVIL_VAR}", None, "/") == "legitX-Injected: evil"

    def test_strips_crlf_from_session_id(self):
        assert _expand_template("${session_id}", "good\r\nX-Injected: evil", "/") == "goodX-Injected: evil"

    def test_strips_lf_only(self):
        assert _expand_template("value\ninjected", None, "/") == "valueinjected"

    def test_strips_cr_only(self):
        assert _expand_template("value\rinjected", None, "/") == "valueinjected"

    def test_strips_nul_byte(self):
        assert _expand_template("value\x00injected", None, "/") == "valueinjected"


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


class TestMergeForwardedHeaders:
    """Tests for the merge logic used at the integration site in anthropic_processor."""

    def test_returns_base_when_upstream_is_none(self):
        base = {"anthropic-beta": "x"}
        assert merge_forwarded_headers(base=base, upstream=None) is base

    def test_returns_base_when_upstream_is_empty(self):
        base = {"anthropic-beta": "x"}
        assert merge_forwarded_headers(base=base, upstream={}) is base

    def test_returns_upstream_when_base_is_none(self):
        upstream = {"Helicone-Auth": "Bearer x"}
        # Aliased by reference per merge_forwarded_headers' contract.
        assert merge_forwarded_headers(base=None, upstream=upstream) is upstream

    def test_returns_upstream_when_base_is_empty(self):
        upstream = {"Helicone-Auth": "Bearer x"}
        assert merge_forwarded_headers(base={}, upstream=upstream) is upstream

    def test_returns_none_when_both_empty(self):
        assert merge_forwarded_headers(base=None, upstream=None) is None
        assert merge_forwarded_headers(base={}, upstream={}) is None

    def test_base_wins_on_case_insensitive_collision(self):
        # SDK adds anthropic-beta; upstream config (mistakenly) tries to override with Anthropic-Beta.
        base = {"anthropic-beta": "sdk-value"}
        upstream = {"Anthropic-Beta": "config-value", "Helicone-Auth": "Bearer x"}
        merged = merge_forwarded_headers(base=base, upstream=upstream)
        assert merged == {"anthropic-beta": "sdk-value", "Helicone-Auth": "Bearer x"}
        # Ensure no duplicate logical header on the wire.
        assert "Anthropic-Beta" not in merged

    def test_non_colliding_headers_pass_through(self):
        base = {"anthropic-beta": "x"}
        upstream = {"Helicone-Auth": "Bearer y", "Helicone-Session-Id": "s"}
        merged = merge_forwarded_headers(base=base, upstream=upstream)
        assert merged == {
            "anthropic-beta": "x",
            "Helicone-Auth": "Bearer y",
            "Helicone-Session-Id": "s",
        }


class TestHeliconeFullConfig:
    """Integration-style test for the original sjawhar Helicone scenario."""

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
