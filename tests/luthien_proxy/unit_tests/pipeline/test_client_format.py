"""Unit tests for ClientFormat enum."""

from luthien_proxy.pipeline.client_format import ClientFormat


class TestClientFormat:
    """Tests for the ClientFormat enumeration."""

    def test_anthropic_value(self):
        """Test ANTHROPIC enum has correct string value."""
        assert ClientFormat.ANTHROPIC.value == "anthropic"

    def test_is_string_enum(self):
        """Test that ClientFormat is a string enum."""
        assert isinstance(ClientFormat.ANTHROPIC, str)

    def test_string_comparison(self):
        """Test ClientFormat can be compared as strings."""
        assert ClientFormat.ANTHROPIC == "anthropic"

    def test_enum_members(self):
        """Test all expected enum members exist."""
        members = list(ClientFormat)
        assert len(members) == 1
        assert ClientFormat.ANTHROPIC in members

    def test_can_use_in_dict_key(self):
        """Test ClientFormat can be used as dictionary key."""
        formatters = {
            ClientFormat.ANTHROPIC: "anthropic_formatter",
        }
        assert formatters[ClientFormat.ANTHROPIC] == "anthropic_formatter"
