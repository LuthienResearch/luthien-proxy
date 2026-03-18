"""Tests for claude command."""

from unittest.mock import patch

from click.testing import CliRunner

from luthien_cli.main import cli


def test_claude_oauth_passthrough_by_default(tmp_path):
    """Without any API key, Claude Code uses OAuth passthrough."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("os.execvpe") as mock_exec,
    ):
        result = runner.invoke(cli, ["claude", "--", "--model", "opus"])
        mock_exec.assert_called_once()
        env = mock_exec.call_args[0][2]
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:9000/"
        assert "ANTHROPIC_API_KEY" not in env
        assert "oauth passthrough" in result.output.lower()


def test_claude_sends_api_key_from_config(tmp_path):
    """With gateway.api_key in config, it sets ANTHROPIC_API_KEY."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\napi_key = "sk-proxy"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("os.execvpe") as mock_exec,
    ):
        result = runner.invoke(cli, ["claude"])
        mock_exec.assert_called_once()
        env = mock_exec.call_args[0][2]
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:9000/"
        assert env["ANTHROPIC_API_KEY"] == "sk-proxy"
        assert "proxy api key" in result.output.lower()


def test_claude_cli_api_key_overrides_config(tmp_path):
    """--api-key flag overrides the config file api_key."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\napi_key = "sk-from-config"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("os.execvpe") as mock_exec,
    ):
        result = runner.invoke(cli, ["claude", "--api-key", "sk-from-cli"])
        mock_exec.assert_called_once()
        env = mock_exec.call_args[0][2]
        assert env["ANTHROPIC_API_KEY"] == "sk-from-cli"
        assert "proxy api key" in result.output.lower()


def test_claude_api_key_from_env(tmp_path, monkeypatch):
    """LUTHIEN_API_KEY env var provides the API key."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    monkeypatch.setenv("LUTHIEN_API_KEY", "sk-from-env")
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("os.execvpe") as mock_exec,
    ):
        result = runner.invoke(cli, ["claude"])
        mock_exec.assert_called_once()
        env = mock_exec.call_args[0][2]
        assert env["ANTHROPIC_API_KEY"] == "sk-from-env"
        assert "proxy api key" in result.output.lower()


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
