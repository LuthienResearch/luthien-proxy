"""Unit tests for session ID extraction functions."""

from luthien_proxy.pipeline.session import (
    OPENAI_SESSION_HEADER,
    extract_session_id_from_anthropic_body,
    extract_session_id_from_headers,
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
            OPENAI_SESSION_HEADER: "my-session-123",
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
        headers = {OPENAI_SESSION_HEADER: ""}
        session_id = extract_session_id_from_headers(headers)
        assert session_id is None

    def test_preserves_uuid_format(self):
        """Test UUID session IDs are preserved correctly."""
        uuid_session = "550e8400-e29b-41d4-a716-446655440000"
        headers = {OPENAI_SESSION_HEADER: uuid_session}
        session_id = extract_session_id_from_headers(headers)
        assert session_id == uuid_session

    def test_header_name_constant(self):
        """Test the header name constant is correct."""
        assert OPENAI_SESSION_HEADER == "x-session-id"
