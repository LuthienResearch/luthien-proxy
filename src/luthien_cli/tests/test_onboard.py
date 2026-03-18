"""Tests for onboard command."""

import os
import socket
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from luthien_cli.commands.onboard import (
    _ensure_env,
    _find_free_port,
    _find_free_ports,
    _indent_instructions,
    _is_port_free,
    _write_policy,
)
from luthien_cli.main import cli


def test_indent_instructions():
    text = "Block PII\nRemove emails"
    result = _indent_instructions(text, indent=6)
    assert result == "      Block PII\n      Remove emails"


def test_write_policy(tmp_path):
    _write_policy(str(tmp_path), "Block all PII from responses")
    policy_path = tmp_path / "config" / "policy_config.yaml"
    assert policy_path.exists()
    content = policy_path.read_text()
    assert "SimpleLLMPolicy" in content
    assert "Block all PII from responses" in content
    assert "claude-haiku-4-5" in content


def test_ensure_env_creates_from_scratch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _ensure_env(str(repo), "sk-test-key", "admin-test-key")
    env_content = (repo / ".env").read_text()
    assert "PROXY_API_KEY=sk-test-key" in env_content
    assert "ADMIN_API_KEY=admin-test-key" in env_content
    assert "AUTH_MODE=both" in env_content
    assert "POLICY_SOURCE=file" in env_content


def test_ensure_env_updates_existing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("PROXY_API_KEY=old-key\nADMIN_API_KEY=old-admin\nSOME_OTHER=value\n")
    _ensure_env(str(repo), "sk-new-key", "admin-new-key")
    env_content = (repo / ".env").read_text()
    assert "PROXY_API_KEY=sk-new-key" in env_content
    assert "ADMIN_API_KEY=admin-new-key" in env_content
    assert "SOME_OTHER=value" in env_content
    assert "old-key" not in env_content


def test_ensure_env_uncomments_auth_mode(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("# AUTH_MODE=passthrough\n")
    _ensure_env(str(repo), "sk-key", "admin-key")
    env_content = (repo / ".env").read_text()
    assert "AUTH_MODE=both" in env_content
    assert "# AUTH_MODE" not in env_content


def test_ensure_env_falls_back_to_example(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env.example").write_text("PROXY_API_KEY=placeholder\n# AUTH_MODE=both\n")
    _ensure_env(str(repo), "sk-key", "admin-key")
    env_content = (repo / ".env").read_text()
    assert "PROXY_API_KEY=sk-key" in env_content
    assert "AUTH_MODE=both" in env_content


def _make_repo(tmp_path, name="repo"):
    """Create a fake luthien-proxy repo directory with docker-compose.yaml."""
    repo_path = tmp_path / name
    repo_path.mkdir()
    (repo_path / "docker-compose.yaml").touch()
    return repo_path


def test_onboard_rejects_invalid_repo(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    bad_path = tmp_path / "not-a-repo"
    bad_path.mkdir()

    with patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(cli, ["onboard"], input=f"{bad_path}\n")

    assert result.exit_code != 0
    assert "docker-compose.yaml" in result.output


def test_onboard_full_flow(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = _make_repo(tmp_path)
    (repo_path / ".env.example").write_text("PROXY_API_KEY=placeholder\n")

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.subprocess.run") as mock_run,
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
        patch("luthien_cli.commands.onboard._find_free_ports", return_value={"GATEWAY_PORT": "9123"}),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(
            cli,
            ["onboard"],
            input=f"{repo_path}\nBlock PII from all responses\n",
        )

    assert result.exit_code == 0, result.output
    assert "Gateway is running" in result.output
    assert "luthien claude" in result.output
    assert "Block PII" in result.output

    # Verify port overrides were passed to docker compose up
    up_call = [c for c in mock_run.call_args_list if "up" in c.args[0]][0]
    assert up_call.kwargs["env"]["GATEWAY_PORT"] == "9123"

    # Verify CLI config saves the actual gateway URL with the non-default port
    config_content = config_path.read_text()
    assert "9123" in config_content

    # Verify policy was written
    policy = (repo_path / "config" / "policy_config.yaml").read_text()
    assert "Block PII from all responses" in policy


def test_onboard_docker_failure(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = _make_repo(tmp_path)

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=1, stderr="compose error")
        result = runner.invoke(
            cli,
            ["onboard"],
            input=f"{repo_path}\nBlock PII\n",
        )

    assert result.exit_code != 0
    assert "failed" in result.output.lower()


def test_onboard_gateway_unhealthy(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = _make_repo(tmp_path)

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.subprocess.run") as mock_run,
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=False),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(
            cli,
            ["onboard"],
            input=f"{repo_path}\nBlock PII\n",
        )

    assert result.exit_code != 0
    assert "healthy" in result.output.lower()


# === Port selection tests ===


def test_is_port_free_on_unbound_port():
    """An unused port should be detected as free."""
    # Use port 0 to let OS pick a free port, then check a nearby one
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        _, free_port = s.getsockname()
    # Port is now unbound, should be free
    assert _is_port_free(free_port) is True


def test_is_port_free_on_bound_port():
    """A port in use should be detected as not free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        _, bound_port = s.getsockname()
        s.listen(1)
        assert _is_port_free(bound_port) is False


def test_find_free_port_returns_start_when_available():
    """Returns the starting port if it's free."""
    with patch("luthien_cli.commands.onboard._is_port_free", return_value=True):
        assert _find_free_port(5433) == 5433


def test_find_free_port_skips_occupied():
    """Skips occupied ports and returns the next free one."""
    # First two ports busy, third is free
    with patch(
        "luthien_cli.commands.onboard._is_port_free",
        side_effect=[False, False, True],
    ):
        assert _find_free_port(5433) == 5435


def test_find_free_port_raises_after_exhaustion():
    """Raises RuntimeError if no free port found within range."""
    with patch("luthien_cli.commands.onboard._is_port_free", return_value=False):
        with pytest.raises(RuntimeError, match="Could not find a free port"):
            _find_free_port(5433)


def test_find_free_ports_respects_env_vars():
    """Ports already set in environment are not overridden."""
    with patch.dict("os.environ", {"GATEWAY_PORT": "9999"}):
        with patch("luthien_cli.commands.onboard._find_free_port", return_value=5433):
            result = _find_free_ports()
            assert "GATEWAY_PORT" not in result
            # Should still auto-select for POSTGRES_PORT and REDIS_PORT
            assert "POSTGRES_PORT" in result or "REDIS_PORT" in result


def test_find_free_ports_auto_selects():
    """All ports are auto-selected when none are set in env."""
    clean_env = {k: v for k, v in os.environ.items() if k not in ("POSTGRES_PORT", "REDIS_PORT", "GATEWAY_PORT")}
    with patch.dict("os.environ", clean_env, clear=True):
        with patch("luthien_cli.commands.onboard._find_free_port", side_effect=[5433, 6379, 8000]):
            result = _find_free_ports()
            assert result == {"POSTGRES_PORT": "5433", "REDIS_PORT": "6379", "GATEWAY_PORT": "8000"}


def test_onboard_shows_api_key_warning(tmp_path):
    """Success panel includes the API key prompt warning."""
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    repo_path = _make_repo(tmp_path)
    (repo_path / ".env.example").write_text("PROXY_API_KEY=placeholder\n")

    with (
        patch("luthien_cli.commands.onboard.DEFAULT_CONFIG_PATH", config_path),
        patch("luthien_cli.commands.onboard.subprocess.run") as mock_run,
        patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True),
    ):
        mock_run.return_value = MagicMock(returncode=0)
        result = runner.invoke(
            cli,
            ["onboard"],
            input=f"{repo_path}\nBlock PII\n",
        )

    assert result.exit_code == 0, result.output
    assert "Yes" in result.output
    assert "bypass" in result.output.lower()
