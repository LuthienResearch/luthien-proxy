"""Tests for Docker local build fallback when GHCR pull fails."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from luthien_cli.commands.onboard import _onboard_docker
from luthien_cli.repo import ensure_repo_clone


class TestLocalBuildFallback:
    """Test that docker compose pull failure offers local build fallback."""

    def _make_config(self, tmp_path):
        config = MagicMock()
        config.repo_path = str(tmp_path)
        (tmp_path / ".env.example").write_text("CLIENT_API_KEY=placeholder\n")
        return config

    @patch("luthien_cli.commands.onboard._show_results")
    @patch("luthien_cli.commands.onboard.save_config")
    @patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True)
    @patch("luthien_cli.commands.onboard.find_docker_ports", return_value={})
    @patch("luthien_cli.commands.onboard.ensure_repo_clone")
    @patch("luthien_cli.commands.onboard.click.confirm", return_value=True)
    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_fail_offers_local_build(
        self,
        mock_run,
        mock_confirm,
        mock_clone,
        mock_ports,
        mock_healthy,
        mock_save,
        mock_show,
        tmp_path,
    ):
        """When pull fails and user accepts, clone repo and build locally."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        (clone_dir / ".env.example").write_text("")
        mock_clone.return_value = str(clone_dir)

        # First call: pull fails. Subsequent calls: build succeeds, down succeeds, up succeeds.
        mock_run.side_effect = [
            # docker compose pull — fails
            subprocess.CompletedProcess(
                args=["docker", "compose", "pull"],
                returncode=1,
                stdout="",
                stderr="403 Forbidden",
            ),
            # docker compose build — succeeds
            subprocess.CompletedProcess(
                args=["docker", "compose", "build"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            # docker compose down — succeeds
            subprocess.CompletedProcess(
                args=["docker", "compose", "down"],
                returncode=0,
                stdout="",
                stderr="",
            ),
            # docker compose up — succeeds
            subprocess.CompletedProcess(
                args=["docker", "compose", "up"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        ]

        _onboard_docker(console, config, "admin-test")

        # Verify clone was called
        mock_clone.assert_called_once()
        # repo_path stays on the managed artifact dir (not the clone)
        assert config.repo_path == str(tmp_path)
        # Verify build was run against the clone dir
        build_call = mock_run.call_args_list[1]
        assert build_call[0][0] == ["docker", "compose", "build"]
        assert build_call[1]["cwd"] == str(clone_dir)

    @patch("luthien_cli.commands.onboard.click.confirm", return_value=False)
    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_fail_user_declines_suggests_local_mode(
        self,
        mock_run,
        mock_confirm,
        tmp_path,
    ):
        """When pull fails and user declines build, suggest local mode."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=1,
            stdout="",
            stderr="403 Forbidden",
        )

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "admin-test")

        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "luthien onboard" in printed

    @patch("luthien_cli.commands.onboard.ensure_repo_clone")
    @patch("luthien_cli.commands.onboard.click.confirm", return_value=True)
    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_build_fails_suggests_local_mode(
        self,
        mock_run,
        mock_confirm,
        mock_clone,
        tmp_path,
    ):
        """When local build also fails, suggest local mode."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        (clone_dir / ".env.example").write_text("")
        mock_clone.return_value = str(clone_dir)

        mock_run.side_effect = [
            # docker compose pull — fails
            subprocess.CompletedProcess(
                args=["docker", "compose", "pull"],
                returncode=1,
                stdout="",
                stderr="403 Forbidden",
            ),
            # docker compose build — also fails
            subprocess.CompletedProcess(
                args=["docker", "compose", "build"],
                returncode=1,
                stdout="",
                stderr="build error: some failure",
            ),
        ]

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "admin-test")

        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "luthien onboard" in printed
        assert "build failed" in printed.lower()

    @patch("luthien_cli.commands.onboard._show_results")
    @patch("luthien_cli.commands.onboard.save_config")
    @patch("luthien_cli.commands.onboard.wait_for_healthy", return_value=True)
    @patch("luthien_cli.commands.onboard.find_docker_ports", return_value={})
    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_succeeds_no_fallback_offered(
        self,
        mock_run,
        mock_ports,
        mock_healthy,
        mock_save,
        mock_show,
        tmp_path,
    ):
        """When pull succeeds, no fallback prompt is shown."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        # All commands succeed
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=0,
            stdout="",
            stderr="",
        )

        _onboard_docker(console, config, "admin-test")

        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "Could not pull" not in printed


class TestEnsureRepoClone:
    """Test the repo clone helper."""

    @patch("luthien_cli.repo.subprocess.run")
    @patch("luthien_cli.repo.shutil.which", return_value="/usr/bin/git")
    @patch("luthien_cli.repo.CLONE_DIR")
    def test_clones_fresh_repo(self, mock_clone_dir, mock_which, mock_run, tmp_path):
        """First call clones the repo with --depth 1."""
        dest = tmp_path / "clone"
        mock_clone_dir.__truediv__ = lambda self, x: dest / x
        # Make Path-like attributes work
        type(mock_clone_dir).is_dir = lambda self: False  # no .git dir
        mock_clone_dir.configure_mock(**{"__str__": lambda self: str(dest)})

        # Use a real Path for CLONE_DIR
        with patch("luthien_cli.repo.CLONE_DIR", dest):
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "clone"],
                returncode=0,
                stdout="",
                stderr="",
            )

            result = ensure_repo_clone()

        assert result == str(dest)
        clone_call = mock_run.call_args[0][0]
        assert "clone" in clone_call
        assert "--depth" in clone_call

    @patch("luthien_cli.repo.subprocess.run")
    @patch("luthien_cli.repo.shutil.which", return_value="/usr/bin/git")
    def test_updates_existing_repo_with_fetch_reset(self, mock_which, mock_run, tmp_path):
        """When .git dir exists, uses fetch+reset instead of cloning."""
        dest = tmp_path / "clone"
        dest.mkdir()
        (dest / ".git").mkdir()

        with patch("luthien_cli.repo.CLONE_DIR", dest):
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "fetch"],
                returncode=0,
                stdout="",
                stderr="",
            )

            result = ensure_repo_clone()

        assert result == str(dest)
        # Should have called fetch then reset (two subprocess.run calls)
        assert mock_run.call_count == 2
        fetch_call = mock_run.call_args_list[0][0][0]
        reset_call = mock_run.call_args_list[1][0][0]
        assert "fetch" in fetch_call
        assert "--depth" in fetch_call
        assert "reset" in reset_call
        assert "--hard" in reset_call

    @patch("luthien_cli.repo.shutil.which", return_value=None)
    def test_no_git_exits(self, mock_which):
        """When git is not found, exits with error."""
        with pytest.raises(SystemExit):
            ensure_repo_clone()

    @patch("luthien_cli.repo.subprocess.run")
    @patch("luthien_cli.repo.shutil.which", return_value="/usr/bin/git")
    def test_clone_failure_exits(self, mock_which, mock_run, tmp_path):
        """When git clone fails, exits with error."""
        dest = tmp_path / "clone"

        with patch("luthien_cli.repo.CLONE_DIR", dest):
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "clone"],
                returncode=1,
                stdout="",
                stderr="fatal: could not access remote",
            )

            with pytest.raises(SystemExit):
                ensure_repo_clone()

    @patch("luthien_cli.repo.subprocess.run")
    @patch("luthien_cli.repo.shutil.which", return_value="/usr/bin/git")
    def test_fetch_failure_continues(self, mock_which, mock_run, tmp_path):
        """When git fetch fails on existing clone, warns but continues."""
        dest = tmp_path / "clone"
        dest.mkdir()
        (dest / ".git").mkdir()

        with patch("luthien_cli.repo.CLONE_DIR", dest):
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "fetch"],
                returncode=1,
                stdout="",
                stderr="network error",
            )

            # Should not raise — uses existing files
            result = ensure_repo_clone()
            assert result == str(dest)
