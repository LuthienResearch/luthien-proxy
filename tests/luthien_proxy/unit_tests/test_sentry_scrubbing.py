"""Tests for Sentry data scrubbing — _summarize() and _sentry_before_send()."""

import pytest

pytestmark = pytest.mark.timeout(10)


def _get_sentry_functions():
    """Import Sentry functions from main.py (function-level import OK in tests)."""
    from luthien_proxy.main import _sentry_before_send, _summarize

    return _summarize, _sentry_before_send


@pytest.fixture(autouse=True)
def enable_sentry(monkeypatch):
    """Enable Sentry for all tests in this module."""
    monkeypatch.setenv("SENTRY_ENABLED", "true")
    monkeypatch.setenv("SENTRY_DSN", "https://fake@sentry.io/0")


class TestSummarize:
    """Tests for _summarize() function."""

    def test_none_returns_none(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(None) is None

    def test_bool_preserved_true(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(True) is True

    def test_bool_preserved_false(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(False) is False

    def test_int_preserved_positive(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(42) == 42

    def test_int_preserved_zero(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(0) == 0

    def test_float_preserved(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(3.14) == 3.14

    def test_str_replaced_with_length(self):
        summarize, _ = _get_sentry_functions()
        assert summarize("hello") == "<str len=5>"

    def test_str_empty(self):
        summarize, _ = _get_sentry_functions()
        assert summarize("") == "<str len=0>"

    def test_bytes_replaced_with_length(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(b"binary data") == "<bytes len=11>"

    def test_bytes_empty(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(b"") == "<bytes len=0>"

    def test_list_replaced_with_length(self):
        summarize, _ = _get_sentry_functions()
        assert summarize([1, 2, 3]) == "<list len=3>"

    def test_list_empty(self):
        summarize, _ = _get_sentry_functions()
        assert summarize([]) == "<list len=0>"

    def test_dict_shows_keys(self):
        summarize, _ = _get_sentry_functions()
        result = summarize({"model": "claude", "messages": []})
        assert result == "<dict keys=['model', 'messages']>"

    def test_dict_keys_truncated_at_8(self):
        summarize, _ = _get_sentry_functions()
        large_dict = {f"key_{i}": i for i in range(20)}
        result = summarize(large_dict)
        assert "key_7" in result
        assert "key_8" not in result

    def test_unknown_type_object(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(object()) == "<object>"

    def test_unknown_type_set(self):
        summarize, _ = _get_sentry_functions()
        assert summarize(set()) == "<set>"


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
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {"exc_info": (KeyboardInterrupt, KeyboardInterrupt(), None)}
        assert before_send(event, hint) is None

    def test_drops_system_exit(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {"exc_info": (SystemExit, SystemExit(0), None)}
        assert before_send(event, hint) is None

    def test_strips_server_name(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {}
        result = before_send(event, hint)
        assert "server_name" not in result

    def test_strips_cookies(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {}
        result = before_send(event, hint)
        assert "cookies" not in result["request"]

    def test_keeps_safe_headers(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {}
        result = before_send(event, hint)
        headers = result["request"]["headers"]
        assert headers["content-type"] == "application/json"
        assert headers["x-request-id"] == "req-123"
        assert headers["accept"] == "application/json"
        assert headers["user-agent"] == "Claude/1.0"

    def test_redacts_auth_headers(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {}
        result = before_send(event, hint)
        headers = result["request"]["headers"]
        assert headers["authorization"] == "[REDACTED]"
        assert headers["x-api-key"] == "[REDACTED]"

    def test_keeps_safe_request_body_keys(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {}
        result = before_send(event, hint)
        data = result["request"]["data"]
        assert data["model"] == "claude-sonnet-4"
        assert data["max_tokens"] == 1024
        assert data["stream"] is True
        assert data["temperature"] == 0.7

    def test_summarizes_llm_content_in_request_body(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {}
        result = before_send(event, hint)
        data = result["request"]["data"]
        assert data["messages"] == "<list len=1>"
        assert data["system"] == "<str len=15>"

    def test_summarizes_string_request_body(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        event["request"]["data"] = '{"model": "claude", "messages": [{"role": "user", "content": "secret prompt"}]}'
        hint = {}
        result = before_send(event, hint)
        assert result["request"]["data"] == "<str len=79>"

    def test_keeps_safe_frame_vars(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {}
        result = before_send(event, hint)
        frame_vars = result["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
        assert frame_vars["call_id"] == "uuid-123"
        assert frame_vars["chunk_count"] == 42
        assert frame_vars["is_streaming"] is True
        assert frame_vars["model"] == "claude-sonnet"

    def test_summarizes_llm_content_vars(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event()
        hint = {}
        result = before_send(event, hint)
        frame_vars = result["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
        assert "dict keys=" in frame_vars["body"]
        assert "dict keys=" in frame_vars["final_response"]
        assert frame_vars["messages"] == "<list len=1>"

    def test_handles_missing_request(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event(include_request=False)
        hint = {}
        result = before_send(event, hint)
        assert "request" not in result
        assert "server_name" not in result

    def test_handles_missing_exception(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event(include_exception=False)
        hint = {}
        result = before_send(event, hint)
        assert "exception" not in result
        assert "server_name" not in result

    def test_handles_empty_frame_vars(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event(frame_vars_empty=True)
        hint = {}
        result = before_send(event, hint)
        frame_vars = result["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
        assert frame_vars == {}

    def test_handles_no_frame_vars_key(self):
        _, before_send = _get_sentry_functions()
        event = self._make_event(include_frame_vars=False)
        hint = {}
        result = before_send(event, hint)
        frame = result["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert "vars" not in frame
