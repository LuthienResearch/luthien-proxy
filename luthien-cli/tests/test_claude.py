"""Tests for claude command."""

from unittest.mock import patch

from click.testing import CliRunner
from luthien_cli.main import cli


def test_claude_sets_env_and_execs(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:9000"\n')
    with (
        patch("luthien_cli.commands.claude.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.claude.shutil.which", return_value="/usr/bin/claude"),
        patch("os.execvpe") as mock_exec,
    ):
        runner.invoke(cli, ["claude", "--", "--model", "opus"])
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args[0][0] == "claude"
        env = call_args[0][2]
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:9000/"


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
