"""Tests for logs command."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from luthien_cli.main import cli


def test_logs_local_mode(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    log_file = tmp_path / "gateway.log"
    log_file.write_text("some log output\n")
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "local"\n'
    )
    with (
        patch("luthien_cli.commands.logs.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.logs.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["logs"])
        assert result.exit_code == 0
        args = mock_run.call_args[0][0]
        assert "tail" in args
        assert str(log_file) in args


def test_logs_docker_mode(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "docker"\n'
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


def test_logs_with_tail_docker(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "docker"\n'
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


def test_logs_local_no_log_file(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "local"\n'
    )
    with patch("luthien_cli.commands.logs.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["logs"])
        assert result.exit_code != 0
        assert "log file" in result.output.lower() or "not found" in result.output.lower()


def test_logs_fails_without_repo_path(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n\n[local]\nmode = "local"\n')
    with patch("luthien_cli.commands.logs.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["logs"])
        assert result.exit_code != 0
