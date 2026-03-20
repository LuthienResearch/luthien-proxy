"""Tests for claude command."""

from unittest.mock import patch

from click.testing import CliRunner

from luthien_cli.commands.claude import ONBOARDING_PROMPT
from luthien_cli.main import cli


def test_claude_oauth_passthrough_by_default(tmp_path):
    """Claude Code always uses OAuth passthrough — no API key in env."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("luthien_cli.commands.claude.webbrowser.open") as mock_browser,
        patch("os.execvpe") as mock_exec,
    ):
        result = runner.invoke(cli, ["claude", "--", "--model", "opus"])
        mock_exec.assert_called_once()
        env = mock_exec.call_args[0][2]
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:9000/"
        assert "ANTHROPIC_API_KEY" not in env
        assert "oauth passthrough" in result.output.lower()
        mock_browser.assert_called_once_with("http://localhost:9000/policy-config")


def test_claude_strips_inherited_api_key(tmp_path, monkeypatch):
    """An inherited ANTHROPIC_API_KEY is always removed to avoid
    Claude Code's 'both token and API key set' conflict warning."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-inherited-from-shell")
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("luthien_cli.commands.claude.webbrowser.open"),
        patch("os.execvpe") as mock_exec,
    ):
        runner.invoke(cli, ["claude"], catch_exceptions=False)
        mock_exec.assert_called_once()
        env = mock_exec.call_args[0][2]
        assert "ANTHROPIC_API_KEY" not in env


def test_claude_strips_config_api_key_too(tmp_path):
    """Even if config has an api_key, it's not passed through — OAuth only."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\napi_key = "sk-proxy"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("luthien_cli.commands.claude.webbrowser.open"),
        patch("os.execvpe") as mock_exec,
    ):
        runner.invoke(cli, ["claude"])
        mock_exec.assert_called_once()
        env = mock_exec.call_args[0][2]
        assert "ANTHROPIC_API_KEY" not in env


def test_claude_fails_when_not_installed(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value=None),
    ):
        result = runner.invoke(cli, ["claude"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "not installed" in result.output.lower()


def test_claude_preseeds_onboarding_prompt(tmp_path):
    """Without explicit prompt, the onboarding prompt is passed as a positional arg
    so Claude Code starts an interactive session with it as the first turn."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("luthien_cli.commands.claude.webbrowser.open"),
        patch("os.execvpe") as mock_exec,
    ):
        runner.invoke(cli, ["claude"])
        args = mock_exec.call_args[0][1]
        # Positional arg (not -p), so it starts interactive
        assert "-p" not in args
        assert ONBOARDING_PROMPT in args


def test_claude_skips_preseed_when_prompt_given(tmp_path):
    """When user passes -p, don't add the onboarding prompt."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("luthien_cli.commands.claude.webbrowser.open"),
        patch("os.execvpe") as mock_exec,
    ):
        runner.invoke(cli, ["claude", "--", "-p", "my custom prompt"])
        args = mock_exec.call_args[0][1]
        assert ONBOARDING_PROMPT not in args
        assert "my custom prompt" in args


def test_claude_opens_config_page(tmp_path):
    """Config page should be opened in browser."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("luthien_cli.commands.claude.webbrowser.open") as mock_browser,
        patch("os.execvpe"),
    ):
        runner.invoke(cli, ["claude"])
        mock_browser.assert_called_once_with("http://localhost:9000/policy-config")
