"""Tests for InferenceProviderRef types and the two parse helpers.

`parse_inference_provider` is the current entry point (accepts the new
YAML shape). `parse_auth_provider` is the deprecated alias retained for
back-compat; it calls the same parser and logs a warning.
"""

import logging

import pytest

from luthien_proxy.credentials import (
    Provider,
    ServerKey,
    UserCredentials,
    UserThenProvider,
    UserThenServer,
    parse_auth_provider,
    parse_inference_provider,
)


class TestParseInferenceProviderNewShape:
    def test_none_returns_user_credentials(self):
        assert isinstance(parse_inference_provider(None), UserCredentials)

    def test_user_credentials_string(self):
        assert isinstance(parse_inference_provider("user_credentials"), UserCredentials)

    def test_provider_shape(self):
        result = parse_inference_provider({"provider": "judge-api-key"})
        assert isinstance(result, Provider)
        assert result.name == "judge-api-key"

    def test_user_then_provider_string_shorthand(self):
        result = parse_inference_provider({"user_then_provider": "fallback-key"})
        assert isinstance(result, UserThenProvider)
        assert result.name == "fallback-key"
        assert result.on_fallback == "warn"

    def test_user_then_provider_dict_with_on_fallback(self):
        result = parse_inference_provider({"user_then_provider": {"name": "my-key", "on_fallback": "fail"}})
        assert isinstance(result, UserThenProvider)
        assert result.name == "my-key"
        assert result.on_fallback == "fail"

    def test_user_then_provider_default_on_fallback(self):
        result = parse_inference_provider({"user_then_provider": {"name": "my-key"}})
        assert isinstance(result, UserThenProvider)
        assert result.on_fallback == "warn"

    def test_unknown_string_raises(self):
        with pytest.raises(ValueError, match="Unknown inference_provider"):
            parse_inference_provider("something_else")

    def test_unknown_dict_raises(self):
        with pytest.raises(ValueError, match="Unknown inference_provider"):
            parse_inference_provider({"unknown_type": "value"})

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError, match="Unknown inference_provider"):
            parse_inference_provider({})

    def test_invalid_on_fallback_raises(self):
        with pytest.raises(ValueError, match="Invalid on_fallback"):
            parse_inference_provider({"user_then_provider": {"name": "key", "on_fallback": "warning"}})

    def test_provider_non_string_name_raises(self):
        with pytest.raises(ValueError, match="provider name must be a string"):
            parse_inference_provider({"provider": 123})

    def test_user_then_provider_non_string_name_raises(self):
        with pytest.raises(ValueError, match="user_then_provider name must be a string"):
            parse_inference_provider({"user_then_provider": {"name": 123}})


class TestParseInferenceProviderLegacyInnerKeys:
    """Legacy inner-key names still parse against the new shape."""

    def test_server_key_alias(self):
        result = parse_inference_provider({"server_key": "judge-api-key"})
        assert isinstance(result, Provider)
        assert result.name == "judge-api-key"

    def test_user_then_server_string_alias(self):
        result = parse_inference_provider({"user_then_server": "fallback-key"})
        assert isinstance(result, UserThenProvider)
        assert result.name == "fallback-key"

    def test_user_then_server_dict_alias(self):
        result = parse_inference_provider({"user_then_server": {"name": "my-key", "on_fallback": "fail"}})
        assert isinstance(result, UserThenProvider)
        assert result.name == "my-key"
        assert result.on_fallback == "fail"

    def test_server_key_non_string_name_raises(self):
        with pytest.raises(ValueError, match="server_key name must be a string"):
            parse_inference_provider({"server_key": 123})


class TestParseAuthProviderDeprecated:
    """`parse_auth_provider` is an alias that logs a deprecation warning."""

    def test_returns_same_result(self):
        via_new = parse_inference_provider({"provider": "x"})
        via_legacy = parse_auth_provider({"provider": "x"})
        assert via_new == via_legacy

    def test_emits_deprecation_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            parse_auth_provider("user_credentials")
        assert any("auth_provider" in rec.message and "inference_provider" in rec.message for rec in caplog.records)

    def test_legacy_inner_keys_still_work(self):
        result = parse_auth_provider({"server_key": "judge-api-key"})
        assert isinstance(result, Provider)
        assert result.name == "judge-api-key"


class TestBackCompatAliases:
    """`ServerKey` and `UserThenServer` must still point at the new types."""

    def test_server_key_is_provider(self):
        assert ServerKey is Provider

    def test_user_then_server_is_user_then_provider(self):
        assert UserThenServer is UserThenProvider


class TestInferenceProviderRefFrozen:
    def test_user_credentials_frozen(self):
        uc = UserCredentials()
        with pytest.raises(AttributeError):
            uc.x = 1  # type: ignore[attr-defined]

    def test_provider_frozen(self):
        p = Provider(name="test")
        with pytest.raises(AttributeError):
            p.name = "changed"  # type: ignore[misc]

    def test_user_then_provider_frozen(self):
        utp = UserThenProvider(name="test")
        with pytest.raises(AttributeError):
            utp.name = "changed"  # type: ignore[misc]


class TestUserThenProviderOnFallback:
    def test_default_is_warn(self):
        assert UserThenProvider(name="x").on_fallback == "warn"

    def test_fallback_mode(self):
        assert UserThenProvider(name="x", on_fallback="fallback").on_fallback == "fallback"

    def test_fail_mode(self):
        assert UserThenProvider(name="x", on_fallback="fail").on_fallback == "fail"
