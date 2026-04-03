"""Tests for AuthProvider types and parse_auth_provider."""

import pytest

from luthien_proxy.credentials import (
    ServerKey,
    UserCredentials,
    UserThenServer,
    parse_auth_provider,
)


class TestParseAuthProvider:
    def test_none_returns_user_credentials(self):
        result = parse_auth_provider(None)
        assert isinstance(result, UserCredentials)

    def test_user_credentials_string(self):
        result = parse_auth_provider("user_credentials")
        assert isinstance(result, UserCredentials)

    def test_server_key(self):
        result = parse_auth_provider({"server_key": "judge-api-key"})
        assert isinstance(result, ServerKey)
        assert result.name == "judge-api-key"

    def test_user_then_server_string_shorthand(self):
        result = parse_auth_provider({"user_then_server": "fallback-key"})
        assert isinstance(result, UserThenServer)
        assert result.name == "fallback-key"
        assert result.on_fallback == "warn"

    def test_user_then_server_dict_with_on_fallback(self):
        result = parse_auth_provider({"user_then_server": {"name": "my-key", "on_fallback": "fail"}})
        assert isinstance(result, UserThenServer)
        assert result.name == "my-key"
        assert result.on_fallback == "fail"

    def test_user_then_server_dict_default_on_fallback(self):
        result = parse_auth_provider({"user_then_server": {"name": "my-key"}})
        assert isinstance(result, UserThenServer)
        assert result.on_fallback == "warn"

    def test_unknown_string_raises(self):
        with pytest.raises(ValueError, match="Unknown auth_provider"):
            parse_auth_provider("something_else")

    def test_unknown_dict_raises(self):
        with pytest.raises(ValueError, match="Unknown auth_provider"):
            parse_auth_provider({"unknown_type": "value"})

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError, match="Unknown auth_provider"):
            parse_auth_provider({})


class TestAuthProviderFrozen:
    def test_user_credentials_frozen(self):
        uc = UserCredentials()
        with pytest.raises(AttributeError):
            uc.x = 1  # type: ignore[attr-defined]

    def test_server_key_frozen(self):
        sk = ServerKey(name="test")
        with pytest.raises(AttributeError):
            sk.name = "changed"  # type: ignore[misc]

    def test_user_then_server_frozen(self):
        uts = UserThenServer(name="test")
        with pytest.raises(AttributeError):
            uts.name = "changed"  # type: ignore[misc]


class TestUserThenServerOnFallback:
    def test_default_is_warn(self):
        assert UserThenServer(name="x").on_fallback == "warn"

    def test_fallback_mode(self):
        assert UserThenServer(name="x", on_fallback="fallback").on_fallback == "fallback"

    def test_fail_mode(self):
        assert UserThenServer(name="x", on_fallback="fail").on_fallback == "fail"
