"""Tests for onboard command."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from luthien_cli.commands.onboard import (
    _ensure_env,
    _indent_instructions,
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

    # Verify policy was written
    policy = (repo_path / "config" / "policy_config.yaml").read_text()
    assert "Block PII from all responses" in policy

    # Verify CLI config was saved
    assert config_path.exists()


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
