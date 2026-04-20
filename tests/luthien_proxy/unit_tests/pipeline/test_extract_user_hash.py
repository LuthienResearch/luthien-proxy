"""Tests for user hash extraction from request metadata."""

import hashlib

from luthien_proxy.credentials import Credential, CredentialType
from luthien_proxy.pipeline.session import extract_user_hash


def test_api_key_mode_extracts_hash():
    """API key mode: user_<hash>_account__session_<uuid> -> <hash>."""
    body = {"metadata": {"user_id": "user_abc123def_account__session_550e8400-e29b-41d4-a716-446655440000"}}
    assert extract_user_hash(body, None) == "abc123def"


def test_api_key_mode_with_underscores_in_hash():
    """Hash part may itself contain underscores — extract up to _account__."""
    body = {"metadata": {"user_id": "user_abc_123_def_account__session_550e8400-e29b-41d4-a716-446655440000"}}
    assert extract_user_hash(body, None) == "abc_123_def"


def test_oauth_mode_hashes_credential():
    """OAuth mode has no stable user ID in metadata — hash the credential."""
    body = {"metadata": {"user_id": '{"device_id":"dev123","session_id":"sess456"}'}}
    cred = Credential(value="oauth-token-abc", credential_type=CredentialType.AUTH_TOKEN)
    result = extract_user_hash(body, cred)
    expected = hashlib.sha256("oauth-token-abc".encode()).hexdigest()[:16]
    assert result == expected


def test_fallback_hashes_credential():
    """No metadata.user_id — fall back to hashing the credential."""
    body = {"model": "claude-opus-4-6", "messages": [], "max_tokens": 1024}
    cred = Credential(value="sk-ant-api-key-123", credential_type=CredentialType.API_KEY)
    result = extract_user_hash(body, cred)
    expected = hashlib.sha256("sk-ant-api-key-123".encode()).hexdigest()[:16]
    assert result == expected


def test_no_metadata_no_credential_returns_none():
    """No metadata and no credential -> None."""
    body = {"model": "claude-opus-4-6", "messages": [], "max_tokens": 1024}
    assert extract_user_hash(body, None) is None


def test_empty_metadata_with_credential():
    """Empty metadata dict — falls back to credential hash."""
    body = {"metadata": {}}
    cred = Credential(value="sk-ant-key", credential_type=CredentialType.API_KEY)
    result = extract_user_hash(body, cred)
    expected = hashlib.sha256("sk-ant-key".encode()).hexdigest()[:16]
    assert result == expected


def test_oauth_mode_without_credential_returns_none():
    """OAuth mode (JSON user_id) with no credential -> None."""
    body = {"metadata": {"user_id": '{"device_id":"dev123","session_id":"sess456"}'}}
    assert extract_user_hash(body, None) is None


def test_unrecognized_user_id_format_falls_back_to_credential():
    """Unrecognized user_id string (not API key pattern) with a credential -> hash credential."""
    body = {"metadata": {"user_id": "some_unknown_format"}}
    cred = Credential(value="sk-ant-fallback", credential_type=CredentialType.API_KEY)
    result = extract_user_hash(body, cred)
    expected = hashlib.sha256("sk-ant-fallback".encode()).hexdigest()[:16]
    assert result == expected


def test_adversarial_user_id_rejects_special_chars():
    """Adversarial metadata.user_id with XSS payload must not be captured as user_hash."""
    body = {
        "metadata": {
            "user_id": 'user_"><script>alert(1)</script>_account__session_x'
        }
    }
    cred = Credential(value="sk-ant-safe", credential_type=CredentialType.API_KEY)
    result = extract_user_hash(body, cred)
    expected = hashlib.sha256("sk-ant-safe".encode()).hexdigest()[:16]
    assert result == expected


def test_api_key_mode_with_dashes_in_hash():
    """Hash part may contain dashes — should still match."""
    body = {"metadata": {"user_id": "user_abc-123-def_account__session_550e8400"}}
    assert extract_user_hash(body, None) == "abc-123-def"
