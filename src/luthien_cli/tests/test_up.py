"""Tests for up/down commands."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from rich.console import Console

from luthien_cli.commands.up import is_gateway_healthy, wait_for_healthy
from luthien_cli.main import cli


def test_up_local_mode(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "local"\n'
    )
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=False),
        patch("luthien_cli.commands.up.is_gateway_running", return_value=None),
        patch("luthien_cli.commands.up.start_gateway", return_value=12345),
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
    ):
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0
        assert "healthy" in result.output.lower()


def test_up_docker_mode(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "docker"\n'
    )
    ps_result = MagicMock(returncode=0, stdout="")
    up_result = MagicMock(returncode=0)
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=False),
        patch("luthien_cli.commands.up.find_docker_ports", return_value={"GATEWAY_PORT": "8001"}),
        patch("luthien_cli.commands.up.subprocess.run", side_effect=[ps_result, up_result]),
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
    ):
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0


def test_up_docker_mode_saves_resolved_gateway_url(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "docker"\n'
    )
    ps_result = MagicMock(returncode=0, stdout="")
    up_result = MagicMock(returncode=0)
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=False),
        patch("luthien_cli.commands.up.find_docker_ports", return_value={"GATEWAY_PORT": "8001"}),
        patch("luthien_cli.commands.up.subprocess.run", side_effect=[ps_result, up_result]),
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.up.save_config") as mock_save,
    ):
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0
        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert saved_config.gateway_url == "http://localhost:8001"


def test_up_docker_mode_skips_port_selection_when_gateway_running(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "docker"\n'
    )
    ps_result = MagicMock(returncode=0, stdout="gateway\npostgres\n")
    up_result = MagicMock(returncode=0)
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=False),
        patch("luthien_cli.commands.up.find_docker_ports") as mock_find_ports,
        patch("luthien_cli.commands.up.subprocess.run", side_effect=[ps_result, up_result]),
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.up.save_config") as mock_save,
    ):
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0
        mock_find_ports.assert_not_called()
        mock_save.assert_not_called()


def test_up_docker_mode_restarts_when_only_db_running(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "docker"\n'
    )
    ps_result = MagicMock(returncode=0, stdout="postgres\nredis\n")
    up_result = MagicMock(returncode=0)
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=False),
        patch("luthien_cli.commands.up.find_docker_ports", return_value={}) as mock_find_ports,
        patch("luthien_cli.commands.up.subprocess.run", side_effect=[ps_result, up_result]),
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
    ):
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0
        mock_find_ports.assert_called_once()


def test_up_docker_mode_fails_on_compose_error(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "docker"\n'
    )
    ps_result = MagicMock(returncode=0, stdout="")
    up_result = MagicMock(returncode=1, stderr="port already allocated")
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=False),
        patch("luthien_cli.commands.up.find_docker_ports", return_value={}),
        patch("luthien_cli.commands.up.subprocess.run", side_effect=[ps_result, up_result]),
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
    ):
        result = runner.invoke(cli, ["up"])
        assert result.exit_code != 0
        assert "docker compose up failed" in result.output


def test_up_local_calls_ensure_venv_when_missing(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n\n[local]\nmode = "local"\n')
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=False),
        patch("luthien_cli.commands.up.ensure_gateway_venv", return_value=str(tmp_path)),
        patch("luthien_cli.commands.up.is_gateway_running", return_value=None),
        patch("luthien_cli.commands.up.start_gateway", return_value=12345),
        patch("luthien_cli.commands.up.wait_for_healthy", return_value=True),
    ):
        result = runner.invoke(cli, ["up"])
        assert result.exit_code == 0


def test_down_local_mode(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "local"\n'
    )
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.stop_gateway", return_value=True) as mock_stop,
    ):
        result = runner.invoke(cli, ["down"])
        assert result.exit_code == 0
        mock_stop.assert_called_once()


def test_down_docker_mode(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[gateway]\nurl = "http://localhost:8000"\n\n[local]\nrepo_path = "{tmp_path}"\nmode = "docker"\n'
    )
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
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n\n[local]\nmode = "local"\n')
    with patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["down"])
        assert result.exit_code != 0


# === wait_for_healthy tests ===


def test_wait_for_healthy_without_console():
    mock_response = MagicMock(status_code=200)
    with patch("luthien_cli.commands.up.httpx.get", return_value=mock_response):
        assert wait_for_healthy("http://localhost:8000", timeout=5) is True


def test_wait_for_healthy_with_console():
    mock_response = MagicMock(status_code=200)
    console = Console(force_terminal=False)
    with patch("luthien_cli.commands.up.httpx.get", return_value=mock_response):
        assert wait_for_healthy("http://localhost:8000", timeout=5, console=console) is True


def test_wait_for_healthy_timeout():
    import httpx

    with patch("luthien_cli.commands.up.httpx.get", side_effect=httpx.ConnectError("")):
        assert wait_for_healthy("http://localhost:8000", timeout=1) is False


# === is_gateway_healthy tests ===


def test_is_gateway_healthy_returns_true():
    mock_response = MagicMock(status_code=200)
    with patch("luthien_cli.commands.up.httpx.get", return_value=mock_response):
        assert is_gateway_healthy("http://localhost:8000") is True


def test_is_gateway_healthy_returns_false_on_connect_error():
    import httpx

    with patch("luthien_cli.commands.up.httpx.get", side_effect=httpx.ConnectError("")):
        assert is_gateway_healthy("http://localhost:8000") is False


def test_is_gateway_healthy_returns_false_on_500():
    mock_response = MagicMock(status_code=500)
    with patch("luthien_cli.commands.up.httpx.get", return_value=mock_response):
        assert is_gateway_healthy("http://localhost:8000") is False


# === ensure_gateway_up idempotency test ===


def test_ensure_gateway_up_returns_early_when_healthy(tmp_path):
    """ensure_gateway_up is idempotent — returns immediately if already healthy."""
    from luthien_cli.commands.up import ensure_gateway_up

    config_path = tmp_path / "config.toml"
    config_path.write_text('[gateway]\nurl = "http://localhost:8000"\n')
    console = Console(force_terminal=False)
    with (
        patch("luthien_cli.commands.up.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.up.is_gateway_healthy", return_value=True),
        patch("luthien_cli.commands.up.start_gateway") as mock_start,
    ):
        ensure_gateway_up(console)
        mock_start.assert_not_called()
