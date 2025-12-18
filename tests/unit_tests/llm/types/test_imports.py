"""Tests for import paths and backwards compatibility."""


class TestDirectImports:
    """Test direct imports from new module paths."""

    def test_import_from_types_package(self):
        """Test importing from luthien_proxy.llm.types package."""
        from luthien_proxy.llm.types import (
            AnthropicImageSource,
            AssistantMessage,
            Message,
            Request,
            SystemMessage,
            UserMessage,
        )

        # Verify types are importable
        assert Message is not None
        assert Request is not None
        assert SystemMessage is not None
        assert UserMessage is not None
        assert AssistantMessage is not None
        assert AnthropicImageSource is not None

    def test_import_from_openai_module(self):
        """Test importing from luthien_proxy.llm.types.openai."""
        from luthien_proxy.llm.types.openai import (
            ContentPart,
            FunctionCall,
            ImageContentPart,
            ImageUrl,
            Message,
            MessageContent,
            Request,
            TextContentPart,
            ToolCall,
            ToolMessage,
        )

        assert Message is not None
        assert Request is not None
        assert ContentPart is not None
        assert ImageContentPart is not None
        assert TextContentPart is not None
        assert ImageUrl is not None
        assert MessageContent is not None
        assert FunctionCall is not None
        assert ToolCall is not None
        assert ToolMessage is not None

    def test_import_from_anthropic_module(self):
        """Test importing from luthien_proxy.llm.types.anthropic."""
        from luthien_proxy.llm.types.anthropic import (
            AnthropicContentBlock,
            AnthropicImageBlock,
            AnthropicImageSource,
            AnthropicImageSourceBase64,
            AnthropicImageSourceUrl,
            AnthropicMessage,
            AnthropicResponse,
            AnthropicTextBlock,
            AnthropicToolResultBlock,
            AnthropicToolUseBlock,
            AnthropicUsage,
        )

        assert AnthropicImageSource is not None
        assert AnthropicImageSourceBase64 is not None
        assert AnthropicImageSourceUrl is not None
        assert AnthropicImageBlock is not None
        assert AnthropicTextBlock is not None
        assert AnthropicToolUseBlock is not None
        assert AnthropicToolResultBlock is not None
        assert AnthropicContentBlock is not None
        assert AnthropicMessage is not None
        assert AnthropicUsage is not None
        assert AnthropicResponse is not None


class TestBackwardsCompatibilityImports:
    """Test backwards compatibility imports."""

    def test_import_request_from_messages(self):
        """Test importing Request from luthien_proxy.messages (deprecated path)."""
        from luthien_proxy.messages import Request

        # Should still work
        assert Request is not None

        # Verify it's the same class
        from luthien_proxy.llm.types import Request as TypesRequest

        assert Request is TypesRequest

    def test_import_from_llm_package(self):
        """Test importing from luthien_proxy.llm package."""
        from luthien_proxy.llm import (
            AnthropicImageBlock,
            AnthropicImageSource,
            AssistantMessage,
            Message,
            Request,
            SystemMessage,
            UserMessage,
        )

        assert Message is not None
        assert Request is not None
        assert SystemMessage is not None
        assert UserMessage is not None
        assert AssistantMessage is not None
        assert AnthropicImageSource is not None
        assert AnthropicImageBlock is not None


class TestTypeIdentity:
    """Test that types are the same across import paths."""

    def test_request_identity(self):
        """Test Request is same type across all import paths."""
        from luthien_proxy.llm import Request as LlmRequest
        from luthien_proxy.llm.types import Request as TypesRequest
        from luthien_proxy.llm.types.openai import Request as OpenaiRequest
        from luthien_proxy.messages import Request as MessagesRequest

        assert LlmRequest is TypesRequest
        assert TypesRequest is OpenaiRequest
        assert OpenaiRequest is MessagesRequest

    def test_message_identity(self):
        """Test Message is same type across import paths."""
        from luthien_proxy.llm import Message as LlmMessage
        from luthien_proxy.llm.types import Message as TypesMessage
        from luthien_proxy.llm.types.openai import Message as OpenaiMessage

        assert LlmMessage is TypesMessage
        assert TypesMessage is OpenaiMessage

    def test_anthropic_type_identity(self):
        """Test Anthropic types are same across import paths."""
        from luthien_proxy.llm import AnthropicImageSource as LlmSource
        from luthien_proxy.llm.types import AnthropicImageSource as TypesSource
        from luthien_proxy.llm.types.anthropic import AnthropicImageSource as AnthropicSource

        assert LlmSource is TypesSource
        assert TypesSource is AnthropicSource
