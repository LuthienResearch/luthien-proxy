# ABOUTME: Unit tests for V2 messages module
# ABOUTME: Tests Request message type (policies work directly with LiteLLM's ModelResponse)

"""Tests for V2 message types."""

from luthien_proxy.messages import Request


class TestRequest:
    """Test Request message type."""

    def test_create_basic_request(self):
        """Test creating a basic request."""
        req = Request(model="gpt-4", messages=[{"role": "user", "content": "Hello"}])

        assert req.model == "gpt-4"
        assert req.messages == [{"role": "user", "content": "Hello"}]
        assert req.stream is False
        assert req.max_tokens is None
        assert req.temperature is None

    def test_create_request_with_optional_fields(self):
        """Test creating request with optional fields."""
        req = Request(
            model="claude-3-opus",
            messages=[{"role": "user", "content": "Test"}],
            max_tokens=100,
            temperature=0.7,
            stream=True,
        )

        assert req.model == "claude-3-opus"
        assert req.max_tokens == 100
        assert req.temperature == 0.7
        assert req.stream is True

    def test_request_allows_extra_fields(self):
        """Test that Request allows extra fields (Pydantic extra='allow')."""
        req = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hi"}],
            custom_field="custom_value",
            another_field=123,
        )

        # Extra fields should be accessible
        assert req.model_extra["custom_field"] == "custom_value"
        assert req.model_extra["another_field"] == 123

    def test_request_model_dump(self):
        """Test serializing Request to dict."""
        req = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=50,
            stream=False,
        )

        data = req.model_dump(exclude_none=True)
        assert data["model"] == "gpt-4"
        assert data["messages"] == [{"role": "user", "content": "Hello"}]
        assert data["max_tokens"] == 50
        assert data["stream"] is False
        # None values should be excluded
        assert "temperature" not in data

    def test_request_from_dict(self):
        """Test creating Request from dict."""
        data = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "system", "content": "You are helpful"}],
            "max_tokens": 200,
        }

        req = Request(**data)
        assert req.model == "gpt-3.5-turbo"
        assert len(req.messages) == 1
        assert req.max_tokens == 200
