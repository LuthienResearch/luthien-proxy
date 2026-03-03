"""Tests for logs command."""

from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from luthien_cli.main import cli


def test_logs_runs_docker_compose_logs(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\n'
    )
    with (
        patch("luthien_cli.commands.logs.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.logs.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["logs"])
        assert result.exit_code == 0
        args = mock_run.call_args[0][0]
        assert "docker" in args
        assert "logs" in args


def test_logs_with_tail(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\n'
    )
    with (
        patch("luthien_cli.commands.logs.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.logs.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["logs", "--tail", "50"])
        assert result.exit_code == 0
        args = mock_run.call_args[0][0]
        assert "--tail" in args
        assert "50" in args


def test_logs_with_follow(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\n'
    )
    with (
        patch("luthien_cli.commands.logs.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.logs.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["logs", "-f"])
        assert result.exit_code == 0
        args = mock_run.call_args[0][0]
        assert "-f" in args


def test_logs_fails_without_repo_path(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    with patch("luthien_cli.commands.logs.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["logs"])
        assert result.exit_code != 0
        assert "repo_path" in result.output.lower() or "no repo" in result.output.lower()
