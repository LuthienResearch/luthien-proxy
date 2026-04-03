"""Tests for Credential and CredentialType."""

from datetime import datetime, timezone

import pytest

from luthien_proxy.credentials import Credential, CredentialError, CredentialType


class TestCredentialType:
    def test_enum_values_match_client_cache_vocabulary(self):
        assert CredentialType.API_KEY.value == "api_key"
        assert CredentialType.AUTH_TOKEN.value == "auth_token"

    def test_str_enum_serializes_to_string(self):
        assert str(CredentialType.API_KEY) == "CredentialType.API_KEY"
        assert CredentialType.API_KEY == "api_key"

    def test_roundtrip_from_string(self):
        assert CredentialType("api_key") is CredentialType.API_KEY
        assert CredentialType("auth_token") is CredentialType.AUTH_TOKEN

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            CredentialType("bearer")


class TestCredential:
    def test_create_api_key(self):
        cred = Credential(value="sk-ant-api-test", credential_type=CredentialType.API_KEY)
        assert cred.value == "sk-ant-api-test"
        assert cred.credential_type == CredentialType.API_KEY
        assert cred.platform == "anthropic"
        assert cred.platform_url is None
        assert cred.expiry is None

    def test_create_auth_token(self):
        cred = Credential(
            value="sk-ant-oat-test",
            credential_type=CredentialType.AUTH_TOKEN,
            platform="anthropic",
        )
        assert cred.credential_type == CredentialType.AUTH_TOKEN

    def test_frozen(self):
        cred = Credential(value="test", credential_type=CredentialType.API_KEY)
        with pytest.raises(AttributeError):
            cred.value = "changed"  # type: ignore[misc]

    def test_with_expiry(self):
        exp = datetime(2026, 12, 31, tzinfo=timezone.utc)
        cred = Credential(
            value="test",
            credential_type=CredentialType.AUTH_TOKEN,
            expiry=exp,
        )
        assert cred.expiry == exp

    def test_with_platform_url(self):
        cred = Credential(
            value="test",
            credential_type=CredentialType.API_KEY,
            platform_url="https://custom.api.example.com",
        )
        assert cred.platform_url == "https://custom.api.example.com"

    def test_equality(self):
        a = Credential(value="test", credential_type=CredentialType.API_KEY)
        b = Credential(value="test", credential_type=CredentialType.API_KEY)
        assert a == b

    def test_inequality_different_type(self):
        a = Credential(value="test", credential_type=CredentialType.API_KEY)
        b = Credential(value="test", credential_type=CredentialType.AUTH_TOKEN)
        assert a != b

    def test_hashable(self):
        cred = Credential(value="test", credential_type=CredentialType.API_KEY)
        assert hash(cred) is not None
        s = {cred}
        assert cred in s

    def test_repr_masks_long_value(self):
        cred = Credential(value="sk-ant-api-secret-key-here", credential_type=CredentialType.API_KEY)
        r = repr(cred)
        assert "sk-ant-a..." in r
        assert "secret-key-here" not in r

    def test_repr_masks_short_value(self):
        cred = Credential(value="short", credential_type=CredentialType.API_KEY)
        r = repr(cred)
        assert "***" in r
        assert "short" not in r


class TestCredentialError:
    def test_is_exception(self):
        assert issubclass(CredentialError, Exception)

    def test_message(self):
        err = CredentialError("no credential found")
        assert str(err) == "no credential found"
