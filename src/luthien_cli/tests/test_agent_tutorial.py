"""Tests for the agent-tutorial CLI command."""

from click.testing import CliRunner

from luthien_cli.commands.agent_tutorial import agent_tutorial


def test_agent_tutorial_exits_zero():
    result = CliRunner().invoke(agent_tutorial)
    assert result.exit_code == 0


def test_agent_tutorial_contains_key_sections():
    result = CliRunner().invoke(agent_tutorial)
    output = result.output
    assert "Agent Tutorial" in output
    assert "Managing Policies via the CLI" in output
    assert "Writing a New Policy" in output
    assert "Critical Constraints" in output
    assert "Workflow" in output


def test_agent_tutorial_contains_hook_signatures():
    result = CliRunner().invoke(agent_tutorial)
    output = result.output
    assert "on_anthropic_request" in output
    assert "on_anthropic_response" in output
    assert "on_anthropic_stream_event" in output
    assert "on_anthropic_stream_complete" in output


def test_agent_tutorial_contains_import_paths():
    result = CliRunner().invoke(agent_tutorial)
    output = result.output
    assert "from luthien_proxy.policy_core import BasePolicy, AnthropicHookPolicy" in output
    assert "from luthien_proxy.policy_core import TextModifierPolicy" in output
    assert "from luthien_proxy.policy_core.policy_context import PolicyContext" in output


def test_agent_tutorial_contains_cli_commands():
    result = CliRunner().invoke(agent_tutorial)
    output = result.output
    assert "luthien policy list" in output
    assert "luthien policy set" in output
    assert "luthien policy show" in output
    assert "luthien policy current" in output
