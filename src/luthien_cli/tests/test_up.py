"""Tests for up/down commands."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from rich.console import Console

from luthien_cli.commands.up import wait_for_healthy
from luthien_cli.main import cli


def test_up_runs_docker_compose(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\n')
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.subprocess.run") as mock_run,
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0
        mock_run.assert_called()


def test_up_calls_ensure_repo_when_missing(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.ensure_repo", return_value=str(tmp_path)),
        patch("luthien_cli.commands.up.subprocess.run") as mock_run,
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0


def test_down_runs_docker_compose_down(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\n')
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["down"])
        assert result.exit_code == 0


def test_down_fails_without_repo_path(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    with patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["down"])
        assert result.exit_code != 0
        assert "repo_path" in result.output.lower() or "no repo" in result.output.lower()


# === wait_for_healthy tests ===


def test_wait_for_healthy_without_console():
    """Works without a console (no spinner)."""
    mock_response = MagicMock(status_code=200)
    with patch("luthien_cli.commands.up.httpx.get", return_value=mock_response):
        assert wait_for_healthy("http://localhost:8000", timeout=5) is True


def test_wait_for_healthy_with_console():
    """Shows spinner when console is provided."""
    mock_response = MagicMock(status_code=200)
    console = Console(force_terminal=False)
    with patch("luthien_cli.commands.up.httpx.get", return_value=mock_response):
        assert wait_for_healthy("http://localhost:8000", timeout=5, console=console) is True


def test_wait_for_healthy_timeout():
    """Returns False when gateway never becomes healthy."""
    import httpx

    with patch("luthien_cli.commands.up.httpx.get", side_effect=httpx.ConnectError("")):
        assert wait_for_healthy("http://localhost:8000", timeout=1) is False
