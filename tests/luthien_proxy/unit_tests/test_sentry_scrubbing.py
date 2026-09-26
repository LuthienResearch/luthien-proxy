"""Tests for Sentry data scrubbing — _summarize() and _sentry_before_send()."""

import logging

import pytest

from luthien_proxy.observability.sentry import (
    _CONTENT_DEPENDENT_STATUS_CODES,
    _CREDENTIAL_DEPENDENT_STATUS_CODES,
    _PROVIDER_SIDE_STATUS_CODES,
    CREDENTIAL_PASSTHROUGH_TAG,
    PASSTHROUGH_TAG,
    _sentry_before_send,
    _summarize,
)

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
        tags=None,
    ):
        """Build a realistic Sentry event for testing."""
        event = {}
        if tags is not None:
            event["tags"] = tags

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

    def test_drops_keyboard_interrupt_with_real_exc_info(self):
        import sys

        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            real_exc_info = sys.exc_info()

        event = self._make_event()
        assert _sentry_before_send(event, {"exc_info": real_exc_info}) is None

    def test_non_tuple_exc_info_does_not_crash(self):
        event = self._make_event()
        result = _sentry_before_send(event, {"exc_info": "not-a-tuple"})
        assert result is not None

    def _status_error(self, status_code: int):
        """Build a real Anthropic status error, as the SDK raises it."""
        import httpx
        from anthropic import APIStatusError

        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(status_code, request=request, json={"error": {"message": "upstream"}})
        return APIStatusError("upstream", response=response, body=None)

    def test_drops_upstream_rate_limit_error(self):
        """A 429 from Anthropic is the upstream telling us to slow down, not a proxy
        bug. The SDK integration captures it unhandled at the call site, which burned
        56 events in three days and buries real failures."""
        exc = self._status_error(429)
        event = self._make_event()
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is None

    def test_drops_upstream_overloaded_error(self):
        exc = self._status_error(529)
        event = self._make_event()
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is None

    def test_drops_upstream_bad_request_error(self):
        """A 400 means the client sent content Anthropic rejects (e.g. an unsupported
        field). Dropped only when the request carrying the PASSTHROUGH_TAG proves the
        proxy relayed it unchanged — see LUTHIEN-6, 1,080 events for one recurring case."""
        exc = self._status_error(400)
        event = self._make_event(tags={PASSTHROUGH_TAG: True})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is None

    def test_drops_bad_request_error_regardless_of_credential_tag(self):
        """400 is credential-independent: a client-key-mode request (server's
        shared ANTHROPIC_API_KEY forwarded, CREDENTIAL_PASSTHROUGH_TAG False)
        with an unmodified body must still drop a 400 — the operator's
        credential has nothing to do with a malformed message the client
        sent. Regression guard for the credential-provenance fix
        overcorrecting into gating 400/404 too."""
        exc = self._status_error(400)
        event = self._make_event(tags={PASSTHROUGH_TAG: True, CREDENTIAL_PASSTHROUGH_TAG: False})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is None

    def test_drops_upstream_not_found_error(self):
        """A 404 means the client asked for a model Anthropic doesn't have (LUTHIEN-2),
        and the PASSTHROUGH_TAG proves the proxy didn't rewrite the request."""
        exc = self._status_error(404)
        event = self._make_event(tags={PASSTHROUGH_TAG: True})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is None

    def test_drops_not_found_error_regardless_of_credential_tag(self):
        """Same regression guard as the 400 case: an unknown model name is
        credential-independent, so a 404 still drops in client-key mode."""
        exc = self._status_error(404)
        event = self._make_event(tags={PASSTHROUGH_TAG: True, CREDENTIAL_PASSTHROUGH_TAG: False})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is None

    def test_drops_upstream_authentication_error(self):
        """A 401 means the credential passed through to Anthropic was invalid
        (LUTHIEN-D) — the proxy correctly forwarded a bad token, it didn't mint
        one. Dropping requires BOTH PASSTHROUGH_TAG (request untouched) AND
        CREDENTIAL_PASSTHROUGH_TAG (the forwarded credential was the client's
        own, not the operator's shared key)."""
        exc = self._status_error(401)
        event = self._make_event(tags={PASSTHROUGH_TAG: True, CREDENTIAL_PASSTHROUGH_TAG: True})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is None

    def test_keeps_client_key_mode_authentication_error(self):
        """A 401 with an unmodified body (PASSTHROUGH_TAG True) but the
        operator's shared credential forwarded instead of the client's own
        (CREDENTIAL_PASSTHROUGH_TAG False, client-key auth mode) must still
        report — the operator's credential is invalid, not the client's, and
        dropping it would silently hide the outage. This is the deep-review
        finding that PR #809's original PASSTHROUGH_TAG-only check missed."""
        exc = self._status_error(401)
        event = self._make_event(tags={PASSTHROUGH_TAG: True, CREDENTIAL_PASSTHROUGH_TAG: False})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is not None

    def test_keeps_authentication_error_when_credential_tag_absent(self):
        """No CREDENTIAL_PASSTHROUGH_TAG at all (e.g. the tag was never set)
        must fail closed for a 401 — absence of proof is not proof of
        passthrough, same rule PASSTHROUGH_TAG already follows."""
        exc = self._status_error(401)
        event = self._make_event(tags={PASSTHROUGH_TAG: True})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is not None

    def test_keeps_proxy_modified_bad_request_error(self):
        """A 400 where the request was NOT proven to be an unmodified client
        passthrough (PASSTHROUGH_TAG missing or False) must still report — a
        policy hook or header/context injection could have built the invalid
        request that Anthropic rejected, and that's a proxy bug (thermonuclear
        review finding: consensus High on PR #809)."""
        exc = self._status_error(400)
        event = self._make_event(tags={PASSTHROUGH_TAG: False})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is not None

    def test_keeps_proxy_modified_not_found_error(self):
        """Same as the 400 case: a 404 without proven passthrough stays visible."""
        exc = self._status_error(404)
        event = self._make_event(tags={PASSTHROUGH_TAG: False})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is not None

    def test_keeps_proxy_modified_authentication_error(self):
        """Same as the 400 case: a 401 without proven passthrough stays visible."""
        exc = self._status_error(401)
        event = self._make_event(tags={PASSTHROUGH_TAG: False})
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is not None

    def test_keeps_bad_request_error_when_passthrough_tag_absent(self):
        """No PASSTHROUGH_TAG at all (e.g. the tag was never set) must fail
        closed — absence of proof is not proof of passthrough."""
        exc = self._status_error(400)
        event = self._make_event()
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is not None

    # ------------------------------------------------------------------
    # Decision-table coverage. Parametrized directly off the production
    # status-code sets in observability/sentry.py, so a status silently
    # added to (or removed from) one of those sets changes which cases these
    # tests run without anyone having to remember to update a hand-written
    # list here. The named tests above stay as regression pins for specific
    # incidents; these supplement them with exhaustive tag-combination
    # coverage.
    # ------------------------------------------------------------------

    _TAG_STATES = (True, False, None)  # None means the tag is absent entirely

    def _tags_for(self, passthrough: bool | None, credential: bool | None) -> dict[str, object]:
        tags: dict[str, object] = {}
        if passthrough is not None:
            tags[PASSTHROUGH_TAG] = passthrough
        if credential is not None:
            tags[CREDENTIAL_PASSTHROUGH_TAG] = credential
        return tags

    @pytest.mark.parametrize("status", sorted(_PROVIDER_SIDE_STATUS_CODES))
    def test_decision_table_provider_side_drops_with_no_tags(self, status):
        """Every status in _PROVIDER_SIDE_STATUS_CODES drops unconditionally —
        no PASSTHROUGH_TAG or CREDENTIAL_PASSTHROUGH_TAG needed. Parametrized
        from the set itself, so adding a status there without exercising it
        here is not possible."""
        exc = self._status_error(status)
        event = self._make_event()
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is None

    @pytest.mark.parametrize("status", sorted(_CONTENT_DEPENDENT_STATUS_CODES))
    @pytest.mark.parametrize("credential", _TAG_STATES, ids=lambda v: f"credential={v}")
    @pytest.mark.parametrize("passthrough", _TAG_STATES, ids=lambda v: f"passthrough={v}")
    def test_decision_table_content_dependent(self, passthrough, credential, status):
        """400/404 drop iff PASSTHROUGH_TAG is True; the credential tag never
        matters, in every one of the 9 tag combinations — pinning the
        deliberate fix that content-only statuses don't gate on credential
        provenance."""
        exc = self._status_error(status)
        event = self._make_event(tags=self._tags_for(passthrough, credential))
        result = _sentry_before_send(event, {"exc_info": (type(exc), exc, None)})
        if passthrough is True:
            assert result is None
        else:
            assert result is not None

    @pytest.mark.parametrize("status", sorted(_CREDENTIAL_DEPENDENT_STATUS_CODES))
    @pytest.mark.parametrize("credential", _TAG_STATES, ids=lambda v: f"credential={v}")
    @pytest.mark.parametrize("passthrough", _TAG_STATES, ids=lambda v: f"passthrough={v}")
    def test_decision_table_credential_dependent(self, passthrough, credential, status):
        """401 drops only when BOTH PASSTHROUGH_TAG and CREDENTIAL_PASSTHROUGH_TAG
        are True; every other one of the 9 combinations — including either
        tag simply missing — must fail closed and still report."""
        exc = self._status_error(status)
        event = self._make_event(tags=self._tags_for(passthrough, credential))
        result = _sentry_before_send(event, {"exc_info": (type(exc), exc, None)})
        if passthrough is True and credential is True:
            assert result is None
        else:
            assert result is not None

    def test_keeps_upstream_status_code_outside_expected_set(self):
        """A status code we have not classified as expected (e.g. 403) still reports,
        so a new upstream failure mode is visible until someone evaluates it."""
        exc = self._status_error(403)
        event = self._make_event()
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is not None

    def test_keeps_proxy_bugs(self):
        """An ordinary exception from our own code must still report."""
        exc = TypeError("Object of type datetime is not JSON serializable")
        event = self._make_event()
        assert _sentry_before_send(event, {"exc_info": (type(exc), exc, None)}) is not None

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

    def test_init_sentry_warns_when_enabled_but_dsn_empty(self, monkeypatch, caplog):
        monkeypatch.setenv("SENTRY_ENABLED", "true")
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        import sentry_sdk

        from luthien_proxy.observability.sentry import init_sentry
        from luthien_proxy.settings import Settings, clear_settings_cache

        clear_settings_cache()
        settings = Settings(_env_file=None)
        with caplog.at_level(logging.WARNING):
            init_sentry(settings)
        assert not sentry_sdk.is_initialized()
        assert "SENTRY_ENABLED=true but SENTRY_DSN is empty" in caplog.text

    def test_init_sentry_warns_when_dsn_is_non_url_string(self, monkeypatch, caplog):
        monkeypatch.setenv("SENTRY_ENABLED", "true")
        monkeypatch.setenv("SENTRY_DSN", "n")
        import sentry_sdk

        from luthien_proxy.observability.sentry import init_sentry
        from luthien_proxy.settings import Settings, clear_settings_cache

        clear_settings_cache()
        settings = Settings(_env_file=None)
        with caplog.at_level(logging.WARNING):
            init_sentry(settings)
        assert not sentry_sdk.is_initialized()
        assert "SENTRY_ENABLED=true but SENTRY_DSN=" in caplog.text
        assert "is not a valid URL" in caplog.text


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
