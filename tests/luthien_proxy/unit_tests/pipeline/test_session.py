"""Unit tests for session ID extraction functions."""

from luthien_proxy.pipeline.session import (
    SESSION_ID_HEADER,
    USER_ID_HEADER,
    extract_session_id_from_anthropic_body,
    extract_session_id_from_headers,
    extract_user_id_from_bearer_token,
    extract_user_id_from_headers,
)


class TestExtractSessionIdFromAnthropicBody:
    """Tests for extract_session_id_from_anthropic_body function."""

    def test_extracts_session_id_from_claude_code_format(self):
        """Test extraction from Claude Code's metadata.user_id format."""
        body = {
            "model": "claude-3-opus",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {
                "user_id": "user_e56b97a3504ae3c04ad0332730777bf378fb75c9ecff9802c098717565372a90_account__session_c31ac7cf-56a7-4c0a-b363-2b726377687d"
            },
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id == "c31ac7cf-56a7-4c0a-b363-2b726377687d"

    def test_returns_none_when_no_metadata(self):
        """Test returns None when metadata field is missing."""
        body = {
            "model": "claude-3-opus",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id is None

    def test_returns_none_when_metadata_not_dict(self):
        """Test returns None when metadata is not a dictionary."""
        body = {
            "model": "claude-3-opus",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": "not_a_dict",
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id is None

    def test_returns_none_when_no_user_id(self):
        """Test returns None when user_id is missing from metadata."""
        body = {
            "model": "claude-3-opus",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"some_other_field": "value"},
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id is None

    def test_returns_none_when_user_id_not_string(self):
        """Test returns None when user_id is not a string."""
        body = {
            "model": "claude-3-opus",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"user_id": 12345},
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id is None

    def test_returns_none_when_user_id_has_no_session(self):
        """Test returns None when user_id doesn't contain session pattern."""
        body = {
            "model": "claude-3-opus",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"user_id": "user_abc123_account"},
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id is None

    def test_extracts_session_id_from_oauth_json_format(self):
        """Test extraction from OAuth mode's JSON-encoded metadata.user_id."""
        body = {
            "model": "claude-3-opus",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {
                "user_id": '{"device_id":"180260606031d2868a596deb2c39d945fc289a03c4492558bd34b7e1eb32ccc1","account_uuid":"e1622933-df71-44ba-9aca-54add8a7ddab","session_id":"f70cfe65-eed9-4ddd-ab51-136673c94e60"}'
            },
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id == "f70cfe65-eed9-4ddd-ab51-136673c94e60"

    def test_returns_none_for_oauth_json_without_session_id(self):
        """Test returns None when OAuth JSON doesn't contain session_id."""
        body = {
            "metadata": {"user_id": '{"device_id":"abc123","account_uuid":"def456"}'},
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id is None

    def test_returns_none_for_oauth_json_with_empty_session_id(self):
        """Test returns None when OAuth JSON has empty session_id."""
        body = {
            "metadata": {"user_id": '{"device_id":"abc123","session_id":""}'},
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id is None

    def test_returns_none_for_invalid_json_user_id(self):
        """Test returns None when user_id is invalid JSON and doesn't match regex."""
        body = {
            "metadata": {"user_id": "not_json_and_no_session_pattern"},
        }
        session_id = extract_session_id_from_anthropic_body(body)
        assert session_id is None

    def test_extracts_different_session_uuids(self):
        """Test extraction works with various UUID formats."""
        test_cases = [
            ("user_hash_account__session_00000000-0000-0000-0000-000000000000", "00000000-0000-0000-0000-000000000000"),
            ("prefix_session_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
            ("_session_12345678-1234-1234-1234-123456789abc", "12345678-1234-1234-1234-123456789abc"),
        ]
        for user_id, expected_session_id in test_cases:
            body = {"metadata": {"user_id": user_id}}
            session_id = extract_session_id_from_anthropic_body(body)
            assert session_id == expected_session_id, f"Failed for user_id: {user_id}"


class TestExtractSessionIdFromHeaders:
    """Tests for extract_session_id_from_headers function."""

    def test_extracts_session_id_from_header(self):
        """Test extraction from x-session-id header."""
        headers = {
            "content-type": "application/json",
            SESSION_ID_HEADER: "my-session-123",
        }
        session_id = extract_session_id_from_headers(headers)
        assert session_id == "my-session-123"

    def test_returns_none_when_header_missing(self):
        """Test returns None when x-session-id header is missing."""
        headers = {
            "content-type": "application/json",
            "authorization": "Bearer token",
        }
        session_id = extract_session_id_from_headers(headers)
        assert session_id is None

    def test_returns_none_if_header_empty(self):
        """Test returns None if header value is empty (normalized for consistent handling)."""
        headers = {SESSION_ID_HEADER: ""}
        session_id = extract_session_id_from_headers(headers)
        assert session_id is None

    def test_preserves_uuid_format(self):
        """Test UUID session IDs are preserved correctly."""
        uuid_session = "550e8400-e29b-41d4-a716-446655440000"
        headers = {SESSION_ID_HEADER: uuid_session}
        session_id = extract_session_id_from_headers(headers)
        assert session_id == uuid_session

    def test_header_name_constant(self):
        """Test the header name constant is correct."""
        assert SESSION_ID_HEADER == "x-session-id"


class TestExtractUserIdFromHeaders:
    """Tests for extract_user_id_from_headers function."""

    def test_extracts_user_id_from_header(self):
        """Test extraction from X-Luthien-User-Id header."""
        headers = {
            "content-type": "application/json",
            USER_ID_HEADER: "alice@example.com",
        }
        user_id = extract_user_id_from_headers(headers, trust_header=True)
        assert user_id == "alice@example.com"

    def test_returns_none_when_header_missing(self):
        """Test returns None when X-Luthien-User-Id header is missing."""
        headers = {
            "content-type": "application/json",
            "authorization": "Bearer token",
        }
        user_id = extract_user_id_from_headers(headers, trust_header=True)
        assert user_id is None

    def test_returns_none_if_header_empty(self):
        """Test returns None if header value is empty."""
        headers = {USER_ID_HEADER: ""}
        user_id = extract_user_id_from_headers(headers, trust_header=True)
        assert user_id is None

    def test_preserves_arbitrary_user_id_format(self):
        """Test arbitrary user ID formats are preserved."""
        for uid in ["user-123", "alice", "alice@corp.example.com", "sub:abc123"]:
            headers = {USER_ID_HEADER: uid}
            assert extract_user_id_from_headers(headers, trust_header=True) == uid

    def test_header_name_constant(self):
        """Test the header name constant is correct."""
        assert USER_ID_HEADER == "x-luthien-user-id"

    def test_returns_none_when_trust_header_false(self):
        """Gating: returns None even when header is present if trust_header=False."""
        headers = {USER_ID_HEADER: "alice@example.com"}
        assert extract_user_id_from_headers(headers, trust_header=False) is None

    def test_strips_control_characters(self):
        """Sanitization: control characters are stripped from the value."""
        headers = {USER_ID_HEADER: "alice\x00\x1f\x7f@example.com"}
        result = extract_user_id_from_headers(headers, trust_header=True)
        assert result == "alice@example.com"

    def test_truncates_to_max_length(self):
        """Sanitization: values longer than 256 chars are truncated."""
        long_id = "a" * 300
        headers = {USER_ID_HEADER: long_id}
        result = extract_user_id_from_headers(headers, trust_header=True)
        assert result == "a" * 256

    def test_returns_none_if_only_control_chars(self):
        """Sanitization: returns None if header contains only control characters."""
        headers = {USER_ID_HEADER: "\x00\x1f\x7f"}
        assert extract_user_id_from_headers(headers, trust_header=True) is None


class TestExtractUserIdFromBearerToken:
    """Tests for extract_user_id_from_bearer_token function."""

    def test_extracts_sub_from_valid_jwt(self):
        """Test extraction of sub claim from a valid JWT."""
        import base64
        import json

        # Build a minimal JWT: header.payload.signature (signature not verified)
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        payload = (
            base64.urlsafe_b64encode(json.dumps({"sub": "user-abc-123", "email": "alice@example.com"}).encode())
            .rstrip(b"=")
            .decode()
        )
        token = f"{header}.{payload}.fakesignature"

        user_id = extract_user_id_from_bearer_token(token)
        assert user_id == "user-abc-123"

    def test_returns_none_when_no_sub_claim(self):
        """Test returns None when JWT has no sub claim."""
        import base64
        import json

        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({"email": "alice@example.com"}).encode()).rstrip(b"=").decode()
        token = f"{header}.{payload}.fakesig"

        user_id = extract_user_id_from_bearer_token(token)
        assert user_id is None

    def test_returns_none_for_non_jwt_token(self):
        """Test returns None for opaque (non-JWT) tokens."""
        user_id = extract_user_id_from_bearer_token("sk-ant-api03-someapikey")
        assert user_id is None

    def test_returns_none_for_empty_token(self):
        """Test returns None for empty token string."""
        user_id = extract_user_id_from_bearer_token("")
        assert user_id is None

    def test_returns_none_for_malformed_jwt_payload(self):
        """Test returns None when JWT payload is not valid base64 JSON."""
        token = "header.notvalidbase64!!.signature"
        user_id = extract_user_id_from_bearer_token(token)
        assert user_id is None

    def test_returns_none_when_sub_is_not_string(self):
        """Test returns None when sub claim is not a string."""
        import base64
        import json

        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({"sub": 12345}).encode()).rstrip(b"=").decode()
        token = f"{header}.{payload}.fakesig"

        user_id = extract_user_id_from_bearer_token(token)
        assert user_id is None

    def test_returns_none_for_none_token(self):
        """Test returns None when token is None."""
        user_id = extract_user_id_from_bearer_token(None)
        assert user_id is None

    def test_handles_jwt_without_padding(self):
        """Test handles JWT base64 without padding (standard JWT format)."""
        import base64
        import json

        # JWT payloads often lack = padding
        payload_data = {"sub": "user-xyz", "iss": "https://auth.example.com"}
        payload_bytes = json.dumps(payload_data).encode()
        # Deliberately strip padding as real JWTs do
        payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
        token = f"eyJhbGciOiJSUzI1NiJ9.{payload}.fakesig"

        user_id = extract_user_id_from_bearer_token(token)
        assert user_id == "user-xyz"

    def _make_jwt(self, sub: str) -> str:
        import base64
        import json

        payload_bytes = json.dumps({"sub": sub}).encode()
        payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
        return f"eyJhbGciOiJSUzI1NiJ9.{payload}.fakesig"

    def test_strips_control_characters_from_sub(self):
        token = self._make_jwt("user\r\nX-Injected: evil")
        assert extract_user_id_from_bearer_token(token) == "userX-Injected: evil"

    def test_truncates_sub_to_max_length(self):
        token = self._make_jwt("a" * 300)
        result = extract_user_id_from_bearer_token(token)
        assert result is not None
        assert len(result) == 256

    def test_returns_none_for_sub_of_only_control_chars(self):
        token = self._make_jwt("\x00\x01\x1f\x7f")
        assert extract_user_id_from_bearer_token(token) is None
