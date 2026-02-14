"""Tests for session-based authentication."""

import time

import pytest

from luthien_proxy.session import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    _create_session_token,
    _get_session_secret,
    _verify_session_token,
    get_login_page_html,
)


class TestSessionSecret:
    def test_derives_consistent_secret(self):
        admin_key = "test-admin-key"
        secret1 = _get_session_secret(admin_key)
        secret2 = _get_session_secret(admin_key)
        assert secret1 == secret2

    def test_different_keys_produce_different_secrets(self):
        secret1 = _get_session_secret("key1")
        secret2 = _get_session_secret("key2")
        assert secret1 != secret2

    def test_secret_is_hex_string(self):
        secret = _get_session_secret("test-key")
        assert len(secret) == 64  # SHA256 produces 64 hex chars
        assert all(c in "0123456789abcdef" for c in secret)


class TestSessionToken:
    def test_create_token_format(self):
        token = _create_session_token("admin-key")
        parts = token.split(".")
        assert len(parts) == 3
        # First part should be a timestamp
        assert parts[0].isdigit()
        # Second part is random
        assert len(parts[1]) > 0
        # Third part is signature
        assert len(parts[2]) == 64  # SHA256 hex

    def test_verify_valid_token(self):
        admin_key = "test-admin-key"
        token = _create_session_token(admin_key)
        assert _verify_session_token(token, admin_key) is True

    def test_verify_invalid_signature(self):
        admin_key = "test-admin-key"
        token = _create_session_token(admin_key)

        # Tamper with the token
        parts = token.split(".")
        parts[2] = "a" * 64  # Replace signature
        tampered = ".".join(parts)

        assert _verify_session_token(tampered, admin_key) is False

    def test_verify_wrong_admin_key(self):
        token = _create_session_token("key1")
        assert _verify_session_token(token, "key2") is False

    def test_verify_expired_token(self):
        admin_key = "test-admin-key"

        # Create a token with an old timestamp
        old_timestamp = str(int(time.time()) - SESSION_MAX_AGE - 100)
        random_id = "testrandomid"
        payload = f"{old_timestamp}.{random_id}"

        import hashlib
        import hmac

        secret = _get_session_secret(admin_key)
        signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        expired_token = f"{payload}.{signature}"

        assert _verify_session_token(expired_token, admin_key) is False

    def test_verify_malformed_tokens(self):
        admin_key = "test-admin-key"

        # Too few parts
        assert _verify_session_token("onlyonepart", admin_key) is False
        assert _verify_session_token("two.parts", admin_key) is False

        # Too many parts
        assert _verify_session_token("one.two.three.four", admin_key) is False

        # Empty string
        assert _verify_session_token("", admin_key) is False

        # Non-numeric timestamp
        assert _verify_session_token("abc.random.sig", admin_key) is False


class TestLoginPageHTML:
    def test_generates_html(self):
        html = get_login_page_html()
        assert "<!DOCTYPE html>" in html
        assert "Luthien Proxy" in html
        assert 'action="/auth/login"' in html

    def test_shows_invalid_error(self):
        html = get_login_page_html(error="invalid")
        assert "Invalid password" in html
        assert 'class="error"' in html

    def test_shows_required_error(self):
        html = get_login_page_html(error="required")
        assert "Login required" in html
        assert 'class="error"' in html

    def test_includes_next_url(self):
        html = get_login_page_html(next_url="/debug/diff")
        assert 'value="/debug/diff"' in html

    def test_escapes_next_url(self):
        # Test that potentially dangerous characters are escaped
        html = get_login_page_html(next_url='/test"><script>alert(1)</script>')
        assert "<script>alert(1)</script>" not in html

    def test_includes_password_toggle(self):
        html = get_login_page_html()
        assert "togglePassword()" in html
        assert 'class="toggle-password"' in html
        assert ">Show<" in html  # Initial button text

    def test_includes_dev_hint(self):
        html = get_login_page_html()
        assert "admin-dev-key" in html
        assert "fillDevKey()" in html
        assert "Production users:" in html

    def test_includes_back_to_gateway_link(self):
        html = get_login_page_html()
        assert "Back to Gateway" in html


class TestValidateNextUrl:
    def test_allows_relative_paths(self):
        from luthien_proxy.session import _validate_next_url

        assert _validate_next_url("/debug/diff") == "/debug/diff"
        assert _validate_next_url("/activity/monitor") == "/activity/monitor"
        assert _validate_next_url("/") == "/"
        assert _validate_next_url("/path?query=value") == "/path?query=value"

    def test_blocks_absolute_http_urls(self):
        from luthien_proxy.session import _validate_next_url

        assert _validate_next_url("http://evil.com") == "/"
        assert _validate_next_url("https://evil.com/phishing") == "/"

    def test_blocks_protocol_relative_urls(self):
        from luthien_proxy.session import _validate_next_url

        assert _validate_next_url("//evil.com") == "/"
        assert _validate_next_url("//evil.com/path") == "/"

    def test_blocks_urls_without_leading_slash(self):
        from luthien_proxy.session import _validate_next_url

        assert _validate_next_url("evil.com") == "/"
        assert _validate_next_url("path/to/page") == "/"

    def test_blocks_urls_with_credentials(self):
        from luthien_proxy.session import _validate_next_url

        assert _validate_next_url("/path@evil.com") == "/"
        assert _validate_next_url("user:pass@evil.com") == "/"

    def test_blocks_unusual_schemes(self):
        from luthien_proxy.session import _validate_next_url

        assert _validate_next_url("javascript://alert(1)") == "/"
        assert _validate_next_url("data://text/html") == "/"

    def test_strips_whitespace(self):
        from luthien_proxy.session import _validate_next_url

        assert _validate_next_url("  /debug/diff  ") == "/debug/diff"
        assert _validate_next_url("\n/path\n") == "/path"


class TestSessionConstants:
    def test_cookie_name_is_string(self):
        assert isinstance(SESSION_COOKIE_NAME, str)
        assert len(SESSION_COOKIE_NAME) > 0

    def test_max_age_is_reasonable(self):
        # Should be at least 1 hour
        assert SESSION_MAX_AGE >= 3600
        # Should be at most 7 days
        assert SESSION_MAX_AGE <= 7 * 24 * 3600


@pytest.fixture
def mock_request():
    """Create a mock request object for testing."""
    from unittest.mock import MagicMock

    request = MagicMock()
    request.cookies = {}
    return request


class TestGetSessionUser:
    def test_returns_none_without_admin_key(self, mock_request):
        from luthien_proxy.session import get_session_user

        result = get_session_user(mock_request, None)
        assert result is None

    def test_returns_none_without_cookie(self, mock_request):
        from luthien_proxy.session import get_session_user

        result = get_session_user(mock_request, "admin-key")
        assert result is None

    def test_returns_token_with_valid_cookie(self, mock_request):
        from luthien_proxy.session import get_session_user

        admin_key = "admin-key"
        token = _create_session_token(admin_key)
        mock_request.cookies = {SESSION_COOKIE_NAME: token}

        result = get_session_user(mock_request, admin_key)
        assert result == token

    def test_returns_none_with_invalid_cookie(self, mock_request):
        from luthien_proxy.session import get_session_user

        mock_request.cookies = {SESSION_COOKIE_NAME: "invalid-token"}

        result = get_session_user(mock_request, "admin-key")
        assert result is None
