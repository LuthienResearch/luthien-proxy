"""Tests for the restart CLI command."""

from unittest.mock import patch

from click.testing import CliRunner

from luthien_cli.commands.restart import restart
from luthien_cli.config import LuthienConfig


def _mock_config(**overrides):
    defaults = dict(
        gateway_url="http://localhost:8000",
        repo_path="/fake/repo",
        mode="local",
        api_key=None,
        admin_key=None,
    )
    defaults.update(overrides)
    return LuthienConfig(**defaults)


@patch("luthien_cli.commands.restart.load_config")
def test_restart_fails_without_repo_path(mock_load):
    mock_load.return_value = _mock_config(repo_path=None)
    result = CliRunner().invoke(restart)
    assert result.exit_code == 1
    assert "No repo_path configured" in result.output


@patch("luthien_cli.commands.up.ensure_gateway_up")
@patch("luthien_cli.commands.restart.stop_gateway")
@patch("luthien_cli.commands.restart.is_gateway_running", return_value=True)
@patch("luthien_cli.commands.restart.load_config")
def test_restart_local_stops_then_starts(mock_load, mock_running, mock_stop, mock_up):
    mock_load.return_value = _mock_config(mode="local")
    result = CliRunner().invoke(restart)
    assert result.exit_code == 0
    mock_stop.assert_called_once()
    assert mock_stop.call_args[0][0] == "/fake/repo"
    mock_up.assert_called_once()


@patch("luthien_cli.commands.up.ensure_gateway_up")
@patch("luthien_cli.commands.restart.is_gateway_running", return_value=False)
@patch("luthien_cli.commands.restart.load_config")
def test_restart_local_no_running_gateway(mock_load, mock_running, mock_up):
    mock_load.return_value = _mock_config(mode="local")
    result = CliRunner().invoke(restart)
    assert result.exit_code == 0
    assert "starting fresh" in result.output
    mock_up.assert_called_once()


@patch("luthien_cli.commands.restart.load_config")
def test_restart_unknown_mode(mock_load):
    mock_load.return_value = _mock_config(mode="unknown")
    result = CliRunner().invoke(restart)
    assert result.exit_code == 1
    assert "Unknown mode" in result.output


@patch("luthien_cli.commands.up.ensure_gateway_up")
@patch("subprocess.run")
@patch("luthien_cli.commands.restart.load_config")
def test_restart_docker_mode(mock_load, mock_subprocess, mock_up):
    mock_load.return_value = _mock_config(mode="docker")
    mock_subprocess.return_value.returncode = 0
    result = CliRunner().invoke(restart)
    assert result.exit_code == 0
    mock_subprocess.assert_called_once()
    call_args = mock_subprocess.call_args
    assert call_args[0][0] == ["docker", "compose", "down"]
    assert call_args[1]["cwd"] == "/fake/repo"
    mock_up.assert_called_once()


@patch("luthien_cli.commands.up.ensure_gateway_up")
@patch("subprocess.run")
@patch("luthien_cli.commands.restart.load_config")
def test_restart_docker_warns_on_failure(mock_load, mock_subprocess, mock_up):
    mock_load.return_value = _mock_config(mode="docker")
    mock_subprocess.return_value.returncode = 1
    mock_subprocess.return_value.stderr = "container not found"
    result = CliRunner().invoke(restart)
    assert result.exit_code == 0
    assert "Warning" in result.output
    mock_up.assert_called_once()
