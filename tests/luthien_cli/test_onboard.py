"""Tests for onboard command."""

import os
import socket
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from luthien_cli.commands.onboard import (
    _ensure_docker_env,
    _write_local_env,
    _write_policy,
)
from luthien_cli.local_process import find_docker_ports as _find_docker_ports
from luthien_cli.local_process import find_free_port as _find_free_port
from luthien_cli.local_process import is_port_free as _is_port_free
from luthien_cli.main import cli


def test_write_policy(tmp_path):
    _write_policy(str(tmp_path), "http://localhost:8000")
    policy_path = tmp_path / "config" / "policy_config.yaml"
    assert policy_path.exists()
    content = policy_path.read_text()
    assert "OnboardingPolicy" in content
    assert "http://localhost:8000" in content


def test_write_local_env_without_admin_key(tmp_path):
    """Default local env omits ADMIN_API_KEY so the gateway uses its default."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_local_env(str(repo), "sk-test-key")
    env_content = (repo / ".env").read_text()
    assert "PROXY_API_KEY=sk-test-key" in env_content
    assert "ADMIN_API_KEY" not in env_content
    assert "AUTH_MODE=both" in env_content
    assert "POLICY_SOURCE=file" in env_content
    assert "sqlite:///" in env_content
    assert "REDIS_URL" not in env_content


def test_write_local_env_with_admin_key(tmp_path):
    """When admin_key is provided, it's written to .env."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_local_env(str(repo), "sk-test-key", admin_key="admin-test-key")
    env_content = (repo / ".env").read_text()
    assert "PROXY_API_KEY=sk-test-key" in env_content
    assert "ADMIN_API_KEY=admin-test-key" in env_content


def test_ensure_docker_env_creates_from_scratch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _ensure_docker_env(str(repo), "sk-test-key", "admin-test-key")
    env_content = (repo / ".env").read_text()
    assert "PROXY_API_KEY=sk-test-key" in env_content
    assert "ADMIN_API_KEY=admin-test-key" in env_content
    assert "AUTH_MODE=both" in env_content
    assert "POLICY_SOURCE=file" in env_content


def test_ensure_docker_env_updates_existing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("PROXY_API_KEY=old-key\nADMIN_API_KEY=old-admin\nSOME_OTHER=value\n")
    _ensure_docker_env(str(repo), "sk-new-key", "admin-new-key")
    env_content = (repo / ".env").read_text()
    assert "PROXY_API_KEY=sk-new-key" in env_content
    assert "ADMIN_API_KEY=admin-new-key" in env_content
    assert "SOME_OTHER=value" in env_content
    assert "old-key" not in env_content


def test_ensure_docker_env_uncomments_auth_mode(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("# AUTH_MODE=passthrough\n")
    _ensure_docker_env(str(repo), "sk-key", "admin-key")
    env_content = (repo / ".env").read_text()
    assert "AUTH_MODE=both" in env_content
    assert "# AUTH_MODE" not in env_content


def test_ensure_docker_env_comments_out_compose_project_name(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("COMPOSE_PROJECT_NAME=luthien-proxy\nOTHER=val\n")
    _ensure_docker_env(str(repo), "sk-key", "admin-key")
    env_content = (repo / ".env").read_text()
    assert "\nCOMPOSE_PROJECT_NAME=" not in env_content
    assert "# COMPOSE_PROJECT_NAME=luthien-proxy" in env_content
    assert "OTHER=val" in env_content


def test_ensure_docker_env_leaves_already_commented_compose_project_name(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("# COMPOSE_PROJECT_NAME=luthien-proxy\n")
    _ensure_docker_env(str(repo), "sk-key", "admin-key")
    env_content = (repo / ".env").read_text()
    assert env_content.count("COMPOSE_PROJECT_NAME") == 1


def test_ensure_docker_env_falls_back_to_example(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env.example").write_text("PROXY_API_KEY=placeholder\n# AUTH_MODE=both\n")
    _ensure_docker_env(str(repo), "sk-key", "admin-key")
    env_content = (repo / ".env").read_text()
    assert "PROXY_API_KEY=sk-key" in env_content
    assert "AUTH_MODE=both" in env_content


def test_onboard_local_full_flow(tmp_path):
    """Test the default local onboard flow (no policy prompt — uses onboarding policy)."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "config").mkdir()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_gateway_venv", return_value=str(repo_path)),
        patch("luthien_cli.commands.onboard.stop_gateway"),
        patch("luthien_cli.commands.onboard.start_gateway", return_value=12345),
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.onboard.find_free_port", return_value=8000),
        patch("luthien_cli.commands.onboard.webbrowser.open"),
    ):
        result = runner.invoke(cli, ["onboard"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    assert "Gateway is running" in result.output
    assert "luthien claude" in result.output

    # Verify config saved with local mode
    config_content = config_path.read_text()
    assert 'mode = "local"' in config_content

    # Verify onboarding policy was written
    policy = (repo_path / "config" / "policy_config.yaml").read_text()
    assert "OnboardingPolicy" in policy

    # Verify .env has sqlite
    env_content = (repo_path / ".env").read_text()
    assert "sqlite:///" in env_content


def test_onboard_docker_full_flow(tmp_path):
    """Test the --docker onboard flow."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "docker-compose.yaml").touch()
    (repo_path / ".env.example").write_text("PROXY_API_KEY=placeholder\n")

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_repo", return_value=str(repo_path)),
        patch("luthien_cli.commands.onboard.subprocess.run") as mock_run,
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.onboard.find_docker_ports", return_value={"GATEWAY_PORT": "9123"}),
        patch("luthien_cli.commands.onboard.webbrowser.open"),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(cli, ["onboard", "--docker"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    assert "Gateway is running" in result.output
    assert "luthien claude" in result.output

    # Verify all three docker compose steps ran (pull, down, up)
    assert mock_run.call_count == 3
    commands = [c.args[0] for c in mock_run.call_args_list]
    assert any("pull" in cmd for cmd in commands)
    assert any("down" in cmd for cmd in commands)
    assert any("up" in cmd for cmd in commands)

    # Verify port overrides were passed to docker compose up
    up_call = [c for c in mock_run.call_args_list if "up" in c.args[0]][0]
    assert up_call.kwargs["env"]["GATEWAY_PORT"] == "9123"

    # Verify CLI config saves the actual gateway URL with the non-default port
    config_content = config_path.read_text()
    assert "9123" in config_content
    assert 'mode = "docker"' in config_content

    # Verify onboarding policy was written with correct gateway URL
    policy = (repo_path / "config" / "policy_config.yaml").read_text()
    assert "OnboardingPolicy" in policy
    assert "9123" in policy


def test_onboard_docker_failure(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "docker-compose.yaml").touch()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_repo", return_value=str(repo_path)),
        patch("luthien_cli.commands.onboard.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=1, stderr="compose error")
        # Answer 'n' to the local build fallback prompt
        result = runner.invoke(cli, ["onboard", "--docker", "-y"], input="n\n")

    assert result.exit_code != 0
    assert "could not pull" in result.output.lower()
    assert "luthien onboard" in result.output


def test_onboard_local_gateway_unhealthy(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "config").mkdir()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_gateway_venv", return_value=str(repo_path)),
        patch("luthien_cli.commands.onboard.stop_gateway"),
        patch("luthien_cli.commands.onboard.start_gateway", return_value=12345),
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=False),
        patch("luthien_cli.commands.onboard.find_free_port", return_value=8000),
    ):
        result = runner.invoke(cli, ["onboard", "-y"])

    assert result.exit_code != 0
    assert "healthy" in result.output.lower()


# === Port selection tests ===


def test_is_port_free_on_unbound_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        _, free_port = s.getsockname()
    assert _is_port_free(free_port) is True


def test_is_port_free_on_bound_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        _, bound_port = s.getsockname()
        s.listen(1)
        assert _is_port_free(bound_port) is False


def test_find_free_port_returns_start_when_available():
    with patch("luthien_cli.local_process.is_port_free", return_value=True):
        assert _find_free_port(5433) == 5433


def test_find_free_port_skips_occupied():
    with patch(
        "luthien_cli.local_process.is_port_free",
        side_effect=[False, False, True],
    ):
        assert _find_free_port(5433) == 5435


def test_find_free_port_raises_after_exhaustion():
    with patch("luthien_cli.local_process.is_port_free", return_value=False):
        with pytest.raises(RuntimeError, match="Could not find a free port"):
            _find_free_port(5433)


def test_find_free_port_skips_excluded():
    with patch("luthien_cli.local_process.is_port_free", return_value=True):
        assert _find_free_port(5433, exclude={5433, 5434}) == 5435


def test_find_docker_ports_respects_env_vars():
    with patch.dict("os.environ", {"GATEWAY_PORT": "9999"}):
        with patch("luthien_cli.local_process.find_free_port", return_value=5433):
            result = _find_docker_ports()
            assert "GATEWAY_PORT" not in result
            assert "POSTGRES_PORT" in result or "REDIS_PORT" in result


def test_find_docker_ports_auto_selects():
    clean_env = {k: v for k, v in os.environ.items() if k not in ("POSTGRES_PORT", "REDIS_PORT", "GATEWAY_PORT")}
    with patch.dict("os.environ", clean_env, clear=True):
        with patch("luthien_cli.local_process.find_free_port", side_effect=[5433, 6379, 8000]):
            result = _find_docker_ports()
            assert result == {"POSTGRES_PORT": "5433", "REDIS_PORT": "6379", "GATEWAY_PORT": "8000"}


def test_onboard_shows_uninstall_instructions(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "config").mkdir()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_gateway_venv", return_value=str(repo_path)),
        patch("luthien_cli.commands.onboard.stop_gateway"),
        patch("luthien_cli.commands.onboard.start_gateway", return_value=12345),
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.onboard.find_free_port", return_value=8000),
        patch("luthien_cli.commands.onboard.webbrowser.open"),
    ):
        result = runner.invoke(cli, ["onboard"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    assert "pipx uninstall" in result.output


def test_onboard_opens_browser(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "config").mkdir()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_gateway_venv", return_value=str(repo_path)),
        patch("luthien_cli.commands.onboard.stop_gateway"),
        patch("luthien_cli.commands.onboard.start_gateway", return_value=12345),
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.onboard.find_free_port", return_value=8000),
        patch("luthien_cli.commands.onboard.webbrowser.open") as mock_browser,
    ):
        result = runner.invoke(cli, ["onboard"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    mock_browser.assert_called_once_with("http://localhost:8000/policy-config")


def test_onboard_local_with_proxy_ref(tmp_path):
    """--proxy-ref is passed through to ensure_gateway_venv."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "config").mkdir()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.ensure_gateway_venv", return_value=str(repo_path)) as mock_venv,
        patch("luthien_cli.commands.onboard.stop_gateway"),
        patch("luthien_cli.commands.onboard.start_gateway", return_value=12345),
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.onboard.find_free_port", return_value=8000),
        patch("luthien_cli.commands.onboard.webbrowser.open"),
    ):
        result = runner.invoke(cli, ["onboard", "--proxy-ref", "abc123"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    mock_venv.assert_called_once_with(proxy_ref="abc123", force_reinstall=True)


def test_onboard_docker_with_proxy_ref_errors(tmp_path):
    """--proxy-ref with --docker should error."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"

    with patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["onboard", "--docker", "--proxy-ref", "abc123", "-y"])

    assert result.exit_code != 0
    assert "docker" in result.output.lower()


def test_onboard_local_with_pr_ref(tmp_path):
    """--proxy-ref '#123' resolves PR before passing to ensure_gateway_venv."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = tmp_path / "managed-repo"
    repo_path.mkdir()
    (repo_path / "config").mkdir()

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.resolve_proxy_ref", return_value="feature/cool") as mock_resolve,
        patch("luthien_cli.commands.onboard.ensure_gateway_venv", return_value=str(repo_path)) as mock_venv,
        patch("luthien_cli.commands.onboard.stop_gateway"),
        patch("luthien_cli.commands.onboard.start_gateway", return_value=12345),
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.onboard.find_free_port", return_value=8000),
        patch("luthien_cli.commands.onboard.webbrowser.open"),
    ):
        result = runner.invoke(cli, ["onboard", "--proxy-ref", "#123"], input="y\nq\n")

    assert result.exit_code == 0, result.output
    mock_resolve.assert_called_once_with("#123")
    mock_venv.assert_called_once_with(proxy_ref="feature/cool", force_reinstall=True)
