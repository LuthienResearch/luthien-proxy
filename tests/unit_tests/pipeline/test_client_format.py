"""Unit tests for ClientFormat enum."""

from luthien_proxy.pipeline.client_format import ClientFormat


class TestClientFormat:
    """Tests for the ClientFormat enumeration."""

    def test_openai_value(self):
        """Test OPENAI enum has correct string value."""
        assert ClientFormat.OPENAI.value == "openai"

    def test_anthropic_value(self):
        """Test ANTHROPIC enum has correct string value."""
        assert ClientFormat.ANTHROPIC.value == "anthropic"

    def test_is_string_enum(self):
        """Test that ClientFormat is a string enum."""
        assert isinstance(ClientFormat.OPENAI, str)
        assert isinstance(ClientFormat.ANTHROPIC, str)

    def test_string_comparison(self):
        """Test ClientFormat can be compared as strings."""
        assert ClientFormat.OPENAI == "openai"
        assert ClientFormat.ANTHROPIC == "anthropic"

    def test_enum_members(self):
        """Test all expected enum members exist."""
        members = list(ClientFormat)
        assert len(members) == 2
        assert ClientFormat.OPENAI in members
        assert ClientFormat.ANTHROPIC in members

    def test_can_use_in_dict_key(self):
        """Test ClientFormat can be used as dictionary key."""
        formatters = {
            ClientFormat.OPENAI: "openai_formatter",
            ClientFormat.ANTHROPIC: "anthropic_formatter",
        }
        assert formatters[ClientFormat.OPENAI] == "openai_formatter"
        assert formatters[ClientFormat.ANTHROPIC] == "anthropic_formatter"
