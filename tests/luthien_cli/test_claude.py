"""Tests for claude command."""

import inspect
from unittest.mock import patch

from click.testing import CliRunner

from luthien_cli.main import cli


def test_claude_oauth_passthrough_by_default(tmp_path):
    """Claude Code always uses OAuth passthrough — no API key in env."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.ensure_gateway_up"),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("os.execvpe") as mock_exec,
    ):
        result = runner.invoke(cli, ["claude", "--", "--model", "opus"])
        mock_exec.assert_called_once()
        env = mock_exec.call_args[0][2]
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:9000/"
        assert "ANTHROPIC_API_KEY" not in env
        assert "oauth passthrough" in result.output.lower()


def test_claude_strips_inherited_api_key(tmp_path, monkeypatch):
    """An inherited ANTHROPIC_API_KEY is always removed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-inherited-from-shell")
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.ensure_gateway_up"),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("os.execvpe") as mock_exec,
    ):
        runner.invoke(cli, ["claude"], catch_exceptions=False)
        mock_exec.assert_called_once()
        env = mock_exec.call_args[0][2]
        assert "ANTHROPIC_API_KEY" not in env


def test_claude_passes_args_through(tmp_path):
    """Extra args are forwarded to claude."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.ensure_gateway_up"),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("os.execvpe") as mock_exec,
    ):
        runner.invoke(cli, ["claude", "--", "-p", "my custom prompt"])
        args = mock_exec.call_args[0][1]
        assert "-p" in args
        assert "my custom prompt" in args


def test_claude_fails_when_not_installed(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.ensure_gateway_up"),
        patch("luthien_cli.commands.claude.shutil.which", return_value=None),
    ):
        result = runner.invoke(cli, ["claude"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "not installed" in result.output.lower()


def test_claude_calls_ensure_gateway_up(tmp_path):
    """luthien claude always calls ensure_gateway_up (which is idempotent)."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.ensure_gateway_up") as mock_up,
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("os.execvpe"),
    ):
        runner.invoke(cli, ["claude"])
        mock_up.assert_called_once()


def test_claude_does_not_import_webbrowser():
    """luthien claude should NOT open a browser — only onboard does that."""
    import luthien_cli.commands.claude as claude_mod

    source = inspect.getsource(claude_mod)
    assert "webbrowser" not in source
