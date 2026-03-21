"""Tests for Sentry data scrubbing — _summarize() and _sentry_before_send()."""

import pytest

from luthien_proxy.observability.sentry import _sentry_before_send, _summarize

pytestmark = pytest.mark.timeout(10)


@pytest.fixture(autouse=True)
def enable_sentry(monkeypatch):
    monkeypatch.setenv("SENTRY_ENABLED", "true")
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/0")


class TestSummarize:
    """Tests for _summarize() function."""

    def test_none_returns_none(self):
        assert _summarize(None) is None

    def test_bool_preserved_true(self):
        assert _summarize(True) is True

    def test_bool_preserved_false(self):
        assert _summarize(False) is False

    def test_int_preserved_positive(self):
        assert _summarize(42) == 42

    def test_int_preserved_zero(self):
        assert _summarize(0) == 0

    def test_float_preserved(self):
        assert _summarize(3.14) == 3.14

    def test_str_replaced_with_length(self):
        assert _summarize("hello") == "<str len=5>"

    def test_str_empty(self):
        assert _summarize("") == "<str len=0>"

    def test_bytes_replaced_with_length(self):
        assert _summarize(b"binary data") == "<bytes len=11>"

    def test_bytes_empty(self):
        assert _summarize(b"") == "<bytes len=0>"

    def test_list_replaced_with_length(self):
        assert _summarize([1, 2, 3]) == "<list len=3>"

    def test_list_empty(self):
        assert _summarize([]) == "<list len=0>"

    def test_dict_shows_keys(self):
        result = _summarize({"model": "claude", "messages": []})
        assert result == "<dict keys=['model', 'messages']>"

    def test_dict_keys_truncated_at_8(self):
        large_dict = {f"key_{i}": i for i in range(20)}
        result = _summarize(large_dict)
        assert "key_7" in result
        assert "key_8" not in result
        assert "..." in result

    def test_dict_no_truncation_indicator_when_8_or_fewer(self):
        result = _summarize({f"key_{i}": i for i in range(8)})
        assert "..." not in result

    def test_unknown_type_object(self):
        assert _summarize(object()) == "<object>"

    def test_unknown_type_set(self):
        assert _summarize(set()) == "<set>"


class TestBeforeSend:
    """Tests for _sentry_before_send() function."""

    def _make_event(
        self,
        exception_type="ValueError",
        include_request=True,
        include_exception=True,
        include_server_name=True,
        include_cookies=True,
        include_frame_vars=True,
        frame_vars_empty=False,
    ):
        """Build a realistic Sentry event for testing."""
        event = {}

        if include_server_name:
            event["server_name"] = "gateway-prod-123"

        if include_request:
            request = {
                "headers": {
                    "content-type": "application/json",
                    "x-request-id": "req-123",
                    "accept": "application/json",
                    "user-agent": "Claude/1.0",
                    "authorization": "Bearer sk-secret-key",
                    "x-api-key": "secret-api-key",
                },
                "data": {
                    "model": "claude-sonnet-4",
                    "max_tokens": 1024,
                    "stream": True,
                    "temperature": 0.7,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "system": "You are helpful",
                },
            }
            if include_cookies:
                request["cookies"] = {"session": "abc123", "tracking": "xyz789"}
            event["request"] = request

        if include_exception:
            frames = []
            if include_frame_vars:
                if frame_vars_empty:
                    frame_vars = {}
                else:
                    frame_vars = {
                        "call_id": "uuid-123",
                        "chunk_count": 42,
                        "is_streaming": True,
                        "model": "claude-sonnet",
                        "body": {"model": "claude", "messages": []},
                        "final_response": {"id": "msg_123", "content": []},
                        "messages": [{"role": "user", "content": "test"}],
                    }
                frames.append({"vars": frame_vars})
            else:
                frames.append({})

            event["exception"] = {
                "values": [
                    {
                        "type": exception_type,
                        "value": "Something went wrong",
                        "stacktrace": {"frames": frames},
                    }
                ]
            }

        return event

    def test_drops_keyboard_interrupt(self):
        event = self._make_event()
        hint = {"exc_info": (KeyboardInterrupt, KeyboardInterrupt(), None)}
        assert _sentry_before_send(event, hint) is None

    def test_drops_system_exit(self):
        event = self._make_event()
        hint = {"exc_info": (SystemExit, SystemExit(0), None)}
        assert _sentry_before_send(event, hint) is None

    def test_strips_server_name(self):
        event = self._make_event()
        hint = {}
        result = _sentry_before_send(event, hint)
        assert "server_name" not in result

    def test_strips_cookies(self):
        event = self._make_event()
        hint = {}
        result = _sentry_before_send(event, hint)
        assert "cookies" not in result["request"]

    def test_keeps_safe_headers(self):
        event = self._make_event()
        hint = {}
        result = _sentry_before_send(event, hint)
        headers = result["request"]["headers"]
        assert headers["content-type"] == "application/json"
        assert headers["x-request-id"] == "req-123"
        assert headers["accept"] == "application/json"
        assert headers["user-agent"] == "Claude/1.0"

    def test_redacts_auth_headers(self):
        event = self._make_event()
        hint = {}
        result = _sentry_before_send(event, hint)
        headers = result["request"]["headers"]
        assert headers["authorization"] == "[REDACTED]"
        assert headers["x-api-key"] == "[REDACTED]"

    def test_keeps_safe_request_body_keys(self):
        event = self._make_event()
        hint = {}
        result = _sentry_before_send(event, hint)
        data = result["request"]["data"]
        assert data["model"] == "claude-sonnet-4"
        assert data["max_tokens"] == 1024
        assert data["stream"] is True
        assert data["temperature"] == 0.7

    def test_summarizes_llm_content_in_request_body(self):
        event = self._make_event()
        hint = {}
        result = _sentry_before_send(event, hint)
        data = result["request"]["data"]
        assert data["messages"] == "<list len=1>"
        assert data["system"] == "<str len=15>"

    def test_summarizes_string_request_body(self):
        event = self._make_event()
        event["request"]["data"] = '{"model": "claude", "messages": [{"role": "user", "content": "secret prompt"}]}'
        result = _sentry_before_send(event, hint={})
        assert result["request"]["data"] == "<str len=79>"

    def test_non_dict_non_string_request_data_does_not_crash(self):
        for value in (None, 42, b"raw bytes", 3.14):
            event = self._make_event()
            event["request"]["data"] = value
            result = _sentry_before_send(event, hint={})
            assert result is not None
            assert result["request"]["data"] == value

    def test_list_request_data_is_summarized(self):
        event = self._make_event()
        event["request"]["data"] = ["item1", "item2"]
        result = _sentry_before_send(event, hint={})
        assert result["request"]["data"] == "<list len=2>"

    def test_keeps_safe_frame_vars(self):
        event = self._make_event()
        hint = {}
        result = _sentry_before_send(event, hint)
        frame_vars = result["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
        assert frame_vars["call_id"] == "uuid-123"
        assert frame_vars["chunk_count"] == 42
        assert frame_vars["is_streaming"] is True
        assert frame_vars["model"] == "claude-sonnet"

    def test_summarizes_llm_content_vars(self):
        event = self._make_event()
        hint = {}
        result = _sentry_before_send(event, hint)
        frame_vars = result["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
        assert "dict keys=" in frame_vars["body"]
        assert "dict keys=" in frame_vars["final_response"]
        assert frame_vars["messages"] == "<list len=1>"

    def test_handles_missing_request(self):
        event = self._make_event(include_request=False)
        hint = {}
        result = _sentry_before_send(event, hint)
        assert "request" not in result
        assert "server_name" not in result

    def test_handles_missing_exception(self):
        event = self._make_event(include_exception=False)
        hint = {}
        result = _sentry_before_send(event, hint)
        assert "exception" not in result
        assert "server_name" not in result

    def test_handles_empty_frame_vars(self):
        event = self._make_event(frame_vars_empty=True)
        hint = {}
        result = _sentry_before_send(event, hint)
        frame_vars = result["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
        assert frame_vars == {}

    def test_handles_no_frame_vars_key(self):
        event = self._make_event(include_frame_vars=False)
        hint = {}
        result = _sentry_before_send(event, hint)
        frame = result["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert "vars" not in frame

    def test_handles_non_dict_headers(self):
        event = self._make_event()
        event["request"]["headers"] = "raw-header-string"
        result = _sentry_before_send(event, hint={})
        assert result["request"]["headers"] == "raw-header-string"

    def test_missing_stacktrace_does_not_crash(self):
        event = self._make_event()
        event["exception"]["values"][0].pop("stacktrace", None)
        result = _sentry_before_send(event, hint={})
        assert result is not None

    def test_non_exception_event_passes_through(self):
        event = {"message": "a log capture", "level": "info"}
        result = _sentry_before_send(event, hint={})
        assert result is not None
        assert result["message"] == "a log capture"

    def test_ignore_logger_called_on_init(self, monkeypatch):
        monkeypatch.setenv("SENTRY_ENABLED", "true")
        monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/0")

        from unittest.mock import patch

        from luthien_proxy.observability.sentry import init_sentry
        from luthien_proxy.settings import Settings, clear_settings_cache

        clear_settings_cache()
        settings = Settings(_env_file=None)

        with (
            patch("luthien_proxy.observability.sentry.sentry_sdk.init"),
            patch("luthien_proxy.observability.sentry.ignore_logger") as mock_ignore,
        ):
            init_sentry(settings)

        mock_ignore.assert_called_once_with("opentelemetry.sdk.trace.export")


class TestSentryDisabledInTests:
    def test_sentry_disabled_by_default_in_tests(self, monkeypatch):
        monkeypatch.delenv("SENTRY_ENABLED", raising=False)
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        from luthien_proxy.settings import Settings

        settings = Settings(_env_file=None)
        assert settings.sentry_enabled is False

    def test_init_sentry_is_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv("SENTRY_ENABLED", raising=False)
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        import sentry_sdk

        from luthien_proxy.observability.sentry import init_sentry
        from luthien_proxy.settings import Settings, clear_settings_cache

        clear_settings_cache()
        settings = Settings(_env_file=None)
        init_sentry(settings)
        assert not sentry_sdk.is_initialized()

    def test_init_sentry_is_noop_when_enabled_but_dsn_empty(self, monkeypatch):
        monkeypatch.setenv("SENTRY_ENABLED", "true")
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        import sentry_sdk

        from luthien_proxy.observability.sentry import init_sentry
        from luthien_proxy.settings import Settings, clear_settings_cache

        clear_settings_cache()
        settings = Settings(_env_file=None)
        init_sentry(settings)
        assert not sentry_sdk.is_initialized()


class TestInitSentryHappyPath:
    def test_init_sentry_calls_sdk_init_with_expected_kwargs(self, monkeypatch):
        monkeypatch.setenv("SENTRY_ENABLED", "true")
        monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/0")
        monkeypatch.setenv("ENVIRONMENT", "production")

        from unittest.mock import patch

        from luthien_proxy.observability.sentry import init_sentry
        from luthien_proxy.settings import Settings, clear_settings_cache

        clear_settings_cache()
        settings = Settings(_env_file=None)

        with patch("luthien_proxy.observability.sentry.sentry_sdk.init") as mock_init:
            init_sentry(settings)

        mock_init.assert_called_once()
        kwargs = mock_init.call_args.kwargs
        assert kwargs["dsn"] == "https://fake@sentry.io/0"
        assert kwargs["send_default_pii"] is False
        assert kwargs["environment"] == "production"
        assert kwargs["before_send"] is not None
        assert kwargs["in_app_include"] == ["luthien_proxy"]
