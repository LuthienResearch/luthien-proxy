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
        assert len(req.messages) == 1
        # Messages are now dicts to support multimodal content (images)
        assert req.messages[0]["role"] == "user"
        assert req.messages[0]["content"] == "Hello"
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
        # Messages are converted to Message objects, so check the serialized structure
        assert len(data["messages"]) == 1
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Hello"
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

    def test_last_message_property(self):
        """Test that last_message property returns the content of the last message."""
        req = Request(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello there"},
            ],
        )

        assert req.last_message == "Hello there"

    def test_last_message_with_none_content(self):
        """Test that last_message handles None content gracefully."""
        req = Request(
            model="gpt-4",
            messages=[
                {"role": "user", "content": "First message"},
                {"role": "assistant", "content": None},
            ],
        )

        assert req.last_message == ""

    def test_last_message_with_multimodal_content(self):
        """Test that last_message extracts text from multimodal content blocks."""
        req = Request(
            model="gpt-4-vision",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo..."}},
                    ],
                }
            ],
        )

        assert req.last_message == "What is in this image?"

    def test_last_message_with_image_only(self):
        """Test that last_message returns empty string for image-only content."""
        req = Request(
            model="gpt-4-vision",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo..."}},
                    ],
                }
            ],
        )

        assert req.last_message == ""

    def test_last_message_with_multiple_text_blocks(self):
        """Test that last_message joins multiple text blocks with spaces."""
        req = Request(
            model="gpt-4-vision",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "First text"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo..."}},
                        {"type": "text", "text": "Second text"},
                    ],
                }
            ],
        )

        assert req.last_message == "First text Second text"
