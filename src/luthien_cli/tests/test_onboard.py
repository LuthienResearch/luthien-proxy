"""Tests for the onboard command helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from luthien_cli.commands.onboard import _ensure_docker_env, _onboard_docker


class TestEnsureDockerEnv:
    """Verify _ensure_docker_env sets all required Docker Compose variables."""

    def test_sets_postgres_vars_from_example(self, tmp_path):
        """Starting from .env.example, all Postgres/Redis vars are uncommented and set."""
        repo = tmp_path / "repo"
        repo.mkdir()
        example = repo / ".env.example"
        example.write_text(
            "# CLIENT_API_KEY=changeme\n"
            "# ADMIN_API_KEY=changeme\n"
            "# POSTGRES_USER=luthien\n"
            "# POSTGRES_PASSWORD=changeme\n"
            "# POSTGRES_DB=luthien_control\n"
            "# POSTGRES_PORT=5433\n"
            "# DATABASE_URL=postgresql://luthien:changeme@db:5432/luthien_control\n"
            "# REDIS_URL=redis://redis:6379\n"
            "# REDIS_PORT=6379\n"
            "AUTH_MODE=client_key\n"
        )

        _ensure_docker_env(str(repo), admin_key="ak-test")

        env_content = (repo / ".env").read_text()

        # Admin key set
        assert "ADMIN_API_KEY=ak-test" in env_content

        # Postgres vars uncommented and populated
        assert "\nPOSTGRES_USER=luthien\n" in env_content
        assert "\nPOSTGRES_DB=luthien_control\n" in env_content
        assert "\nPOSTGRES_PORT=5433\n" in env_content
        assert "\nREDIS_URL=redis://redis:6379\n" in env_content
        assert "\nREDIS_PORT=6379\n" in env_content

        # Password is generated (not "changeme")
        for line in env_content.splitlines():
            if line.startswith("POSTGRES_PASSWORD="):
                password = line.split("=", 1)[1]
                assert password != "changeme"
                assert len(password) > 8
                break
        else:
            raise AssertionError("POSTGRES_PASSWORD not found")

        # DATABASE_URL uses the generated password
        for line in env_content.splitlines():
            if line.startswith("DATABASE_URL="):
                assert password in line
                assert "db:5432/luthien_control" in line
                break
        else:
            raise AssertionError("DATABASE_URL not found")

    def test_sets_vars_even_without_example(self, tmp_path):
        """If no .env or .env.example exists, vars are appended to empty content."""
        repo = tmp_path / "repo"
        repo.mkdir()

        _ensure_docker_env(str(repo), admin_key="ak-test")

        env_content = (repo / ".env").read_text()
        assert "POSTGRES_USER=luthien" in env_content
        assert "REDIS_URL=redis://redis:6379" in env_content
        assert "DATABASE_URL=postgresql://" in env_content

    def test_env_file_permissions(self, tmp_path):
        """The .env file should have 0600 permissions."""
        repo = tmp_path / "repo"
        repo.mkdir()

        _ensure_docker_env(str(repo), admin_key="ak-test")

        env_path = repo / ".env"
        mode = oct(env_path.stat().st_mode & 0o777)
        assert mode == "0o600"


class TestOnboardDockerCloneSystemExit:
    """Verify SystemExit from ensure_repo_clone propagates out of _onboard_docker."""

    def test_ensure_repo_clone_system_exit_propagates(self, tmp_path):
        """If ensure_repo_clone raises SystemExit (e.g. git not installed), it
        should propagate up through _onboard_docker without being caught."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".env.example").write_text("# placeholder\n")

        config = MagicMock()
        config.repo_path = str(repo)

        console = Console(file=MagicMock(), stderr=False)

        # Simulate: docker compose pull fails, user accepts build prompt,
        # but ensure_repo_clone raises SystemExit (e.g. git not found).
        failed_pull = MagicMock(returncode=1, stderr="pull failed")

        with (
            patch(
                "luthien_cli.commands.onboard.subprocess.run",
                return_value=failed_pull,
            ),
            patch("luthien_cli.commands.onboard.click.confirm", return_value=True),
            patch(
                "luthien_cli.commands.onboard.ensure_repo_clone",
                side_effect=SystemExit(1),
            ),
            pytest.raises(SystemExit),
        ):
            _onboard_docker(console, config, "admin-test")
