"""Unit tests for request_log sanitization.

Tests header sanitization to ensure sensitive values are redacted
before persisting to request logs.
"""

from luthien_proxy.request_log.sanitize import sanitize_headers


class TestKnownSensitiveHeaders:
    """Tests for headers that should always be fully redacted."""

    def test_authorization_header_redacted(self):
        headers = {"Authorization": "Bearer sk-1234567890abcdef1234"}
        result = sanitize_headers(headers)
        assert result["Authorization"] == "[REDACTED]"

    def test_x_api_key_header_redacted(self):
        headers = {"x-api-key": "sk-abcdef1234567890abcd"}
        result = sanitize_headers(headers)
        assert result["x-api-key"] == "[REDACTED]"

    def test_proxy_authorization_header_redacted(self):
        headers = {"Proxy-Authorization": "Basic dXNlcjpwYXNz"}
        result = sanitize_headers(headers)
        assert result["Proxy-Authorization"] == "[REDACTED]"

    def test_cookie_header_redacted(self):
        headers = {"Cookie": "session=abc123; user_id=456"}
        result = sanitize_headers(headers)
        assert result["Cookie"] == "[REDACTED]"

    def test_set_cookie_header_redacted(self):
        headers = {"Set-Cookie": "session=xyz789; Path=/; HttpOnly"}
        result = sanitize_headers(headers)
        assert result["Set-Cookie"] == "[REDACTED]"


class TestCaseInsensitivity:
    """Tests that header name matching is case-insensitive."""

    def test_authorization_lowercase(self):
        headers = {"authorization": "Bearer token123"}
        result = sanitize_headers(headers)
        assert result["authorization"] == "[REDACTED]"

    def test_authorization_uppercase(self):
        headers = {"AUTHORIZATION": "Bearer token123"}
        result = sanitize_headers(headers)
        assert result["AUTHORIZATION"] == "[REDACTED]"

    def test_authorization_mixed_case(self):
        headers = {"AuThOrIzAtIoN": "Bearer token123"}
        result = sanitize_headers(headers)
        assert result["AuThOrIzAtIoN"] == "[REDACTED]"

    def test_x_api_key_mixed_case(self):
        headers = {"X-API-KEY": "sk-secret123"}
        result = sanitize_headers(headers)
        assert result["X-API-KEY"] == "[REDACTED]"

    def test_cookie_mixed_case(self):
        headers = {"CoOkIe": "data=value"}
        result = sanitize_headers(headers)
        assert result["CoOkIe"] == "[REDACTED]"


class TestNonSensitiveHeaders:
    """Tests that non-sensitive headers pass through unchanged when they don't contain API keys."""

    def test_content_type_unchanged(self):
        headers = {"Content-Type": "application/json"}
        result = sanitize_headers(headers)
        assert result["Content-Type"] == "application/json"

    def test_user_agent_unchanged(self):
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
        result = sanitize_headers(headers)
        assert result["User-Agent"] == "Mozilla/5.0 (X11; Linux x86_64)"

    def test_accept_unchanged(self):
        headers = {"Accept": "application/json, text/plain"}
        result = sanitize_headers(headers)
        assert result["Accept"] == "application/json, text/plain"

    def test_custom_header_unchanged(self):
        headers = {"X-Custom-Header": "custom value"}
        result = sanitize_headers(headers)
        assert result["X-Custom-Header"] == "custom value"


class TestAPIKeyPatternDetection:
    """Tests for detecting and redacting API key patterns in non-sensitive headers."""

    def test_sk_pattern_redacted(self):
        headers = {"X-Custom": "key is sk-abcdefghijklmnopqrst here"}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == "key is [REDACTED] here"

    def test_anthr_pattern_redacted(self):
        headers = {"X-Custom": "anthr-abcdefghijklmnopqrst123"}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == "[REDACTED]"

    def test_long_hex_pattern_redacted(self):
        headers = {"X-Custom": "abcdef0123456789abcdef0123456789"}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == "[REDACTED]"

    def test_multiple_api_keys_in_header_all_redacted(self):
        headers = {"X-Custom": "sk-abc123def456 and anthr-xyz789abc123"}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == "[REDACTED] and [REDACTED]"

    def test_api_key_at_start_redacted(self):
        headers = {"X-Custom": "sk-abcdefghijklmnopqrst is the key"}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == "[REDACTED] is the key"

    def test_api_key_at_end_redacted(self):
        headers = {"X-Custom": "my key: sk-abcdefghijklmnopqrst"}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == "my key: [REDACTED]"

    def test_short_hex_not_redacted(self):
        headers = {"X-Custom": "abcdef0123456789"}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == "abcdef0123456789"

    def test_sk_pattern_too_short_not_redacted(self):
        headers = {"X-Custom": "sk-short"}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == "sk-short"


class TestEmptyAndEdgeCases:
    """Tests for empty and edge case inputs."""

    def test_empty_headers_dict(self):
        headers = {}
        result = sanitize_headers(headers)
        assert result == {}

    def test_header_with_empty_value(self):
        headers = {"X-Custom": ""}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == ""

    def test_header_with_whitespace_only(self):
        headers = {"X-Custom": "   "}
        result = sanitize_headers(headers)
        assert result["X-Custom"] == "   "

    def test_sensitive_header_with_empty_value(self):
        headers = {"Authorization": ""}
        result = sanitize_headers(headers)
        assert result["Authorization"] == "[REDACTED]"


class TestMixedSensitiveAndNonSensitive:
    """Tests for dicts containing both sensitive and non-sensitive headers."""

    def test_mixed_headers(self):
        headers = {
            "Authorization": "Bearer sk-secret123",
            "Content-Type": "application/json",
            "x-api-key": "key123",
            "User-Agent": "MyApp/1.0",
        }
        result = sanitize_headers(headers)
        assert result["Authorization"] == "[REDACTED]"
        assert result["Content-Type"] == "application/json"
        assert result["x-api-key"] == "[REDACTED]"
        assert result["User-Agent"] == "MyApp/1.0"

    def test_mixed_with_api_key_pattern_in_custom_header(self):
        headers = {
            "Authorization": "Bearer token",
            "Content-Type": "application/json",
            "X-Custom": "value is sk-abcdefghijklmnopqrst",
        }
        result = sanitize_headers(headers)
        assert result["Authorization"] == "[REDACTED]"
        assert result["Content-Type"] == "application/json"
        assert result["X-Custom"] == "value is [REDACTED]"

    def test_multiple_sensitive_headers(self):
        headers = {
            "Authorization": "Bearer token",
            "Cookie": "session=abc",
            "Set-Cookie": "new_session=xyz",
            "x-api-key": "key123",
            "Proxy-Authorization": "Basic auth",
        }
        result = sanitize_headers(headers)
        assert result["Authorization"] == "[REDACTED]"
        assert result["Cookie"] == "[REDACTED]"
        assert result["Set-Cookie"] == "[REDACTED]"
        assert result["x-api-key"] == "[REDACTED]"
        assert result["Proxy-Authorization"] == "[REDACTED]"


class TestNoDictMutation:
    """Tests that the original dict is not mutated."""

    def test_original_dict_unchanged(self):
        original = {"Authorization": "Bearer token", "Content-Type": "application/json"}
        original_copy = original.copy()
        sanitize_headers(original)
        assert original == original_copy

    def test_returns_new_dict(self):
        headers = {"Authorization": "Bearer token"}
        result = sanitize_headers(headers)
        assert result is not headers

    def test_modification_does_not_affect_original(self):
        original = {"Authorization": "Bearer token"}
        result = sanitize_headers(original)
        result["Authorization"] = "modified"
        assert original["Authorization"] == "Bearer token"
