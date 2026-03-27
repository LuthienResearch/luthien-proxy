"""Tests for Docker onboarding error handling."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from luthien_cli.commands.onboard import _onboard_docker
from luthien_cli.repo import _download_files


class TestDockerPullErrorHandling:
    """Test that docker compose pull failures produce helpful error messages."""

    def _make_config(self, tmp_path):
        config = MagicMock()
        config.repo_path = str(tmp_path)
        # Create .env.example so _ensure_docker_env doesn't fail
        (tmp_path / ".env.example").write_text("PROXY_API_KEY=placeholder\n")
        return config

    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_403_shows_access_denied_message(self, mock_run, tmp_path, capsys):
        """When docker compose pull returns 403, show a clear access denied message."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        # Simulate docker compose pull failing with 403
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=1,
            stdout="",
            stderr="Error response from daemon: Head https://ghcr.io/v2/...: unexpected status code 403 Forbidden",
        )

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "sk-test", "admin-test")

        # Check that console.print was called with access denied messaging
        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "access denied" in printed.lower()
        assert "luthien onboard" in printed

    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_unauthorized_shows_access_denied_message(self, mock_run, tmp_path):
        """When docker compose pull returns unauthorized, show access denied message."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=1,
            stdout="",
            stderr="unauthorized: authentication required",
        )

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "sk-test", "admin-test")

        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "access denied" in printed.lower()

    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_forbidden_shows_access_denied_message(self, mock_run, tmp_path):
        """When docker compose pull returns 'forbidden', show access denied message."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=1,
            stdout="",
            stderr="Error: forbidden - access to the resource is denied",
        )

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "sk-test", "admin-test")

        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "access denied" in printed.lower()

    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_access_denied_shows_access_denied_message(self, mock_run, tmp_path):
        """When docker compose pull returns 'access denied', show access denied message."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=1,
            stdout="",
            stderr="denied: requested access to the resource is denied",
        )

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "sk-test", "admin-test")

        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "access denied" in printed.lower()

    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_bare_denied_does_not_match(self, mock_run, tmp_path):
        """Bare 'denied' without 'access' should NOT trigger GHCR auth messaging.

        This avoids false positives for Docker socket 'permission denied' errors.
        """
        config = self._make_config(tmp_path)
        console = MagicMock()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=1,
            stdout="",
            stderr="permission denied while trying to connect to the Docker daemon socket",
        )

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "sk-test", "admin-test")

        printed = " ".join(str(call) for call in console.print.call_args_list)
        # Should show raw stderr, not the GHCR access denied guidance
        assert "permission denied while trying to connect" in printed
        assert "luthien onboard" not in printed

    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_none_stderr_handled_gracefully(self, mock_run, tmp_path):
        """When stderr is None (not empty string), handle gracefully."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=1,
            stdout="",
            stderr=None,
        )

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "sk-test", "admin-test")

        # Should not crash
        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "docker compose pull failed" in printed

    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_generic_failure_shows_raw_stderr(self, mock_run, tmp_path):
        """Non-auth failures should still show the raw stderr."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=1,
            stdout="",
            stderr="network timeout connecting to registry",
        )

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "sk-test", "admin-test")

        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "network timeout" in printed
        # Should NOT show the access denied guidance
        assert "access denied" not in printed.lower()

    @patch("luthien_cli.commands.onboard.subprocess.run")
    def test_pull_empty_stderr_shows_generic_message(self, mock_run, tmp_path):
        """When stderr is empty, still handle gracefully."""
        config = self._make_config(tmp_path)
        console = MagicMock()

        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "compose", "pull"],
            returncode=1,
            stdout="",
            stderr="",
        )

        with pytest.raises(SystemExit):
            _onboard_docker(console, config, "sk-test", "admin-test")

        # Should not crash, should show generic failure message
        printed = " ".join(str(call) for call in console.print.call_args_list)
        assert "docker compose pull failed" in printed


class TestDownloadFiles403:
    """Test that _download_files handles 403 errors with helpful messages."""

    @patch("luthien_cli.repo.httpx.get")
    @patch("luthien_cli.repo.Console")
    def test_download_403_shows_access_denied(self, mock_console_cls, mock_get, tmp_path):
        """HTTP 403 on file download shows access denied message."""
        import httpx

        mock_console = MagicMock()
        mock_console_cls.return_value = mock_console

        response = httpx.Response(403, request=httpx.Request("GET", "https://example.com"))
        mock_get.side_effect = httpx.HTTPStatusError(
            "Forbidden",
            request=response.request,
            response=response,
        )

        with pytest.raises(SystemExit):
            _download_files(tmp_path)

        printed = " ".join(str(call) for call in mock_console.print.call_args_list)
        assert "access denied" in printed.lower()
        assert "403" in printed

    @patch("luthien_cli.repo.httpx.get")
    @patch("luthien_cli.repo.Console")
    def test_download_401_shows_access_denied(self, mock_console_cls, mock_get, tmp_path):
        """HTTP 401 on file download shows access denied message."""
        import httpx

        mock_console = MagicMock()
        mock_console_cls.return_value = mock_console

        response = httpx.Response(401, request=httpx.Request("GET", "https://example.com"))
        mock_get.side_effect = httpx.HTTPStatusError(
            "Unauthorized",
            request=response.request,
            response=response,
        )

        with pytest.raises(SystemExit):
            _download_files(tmp_path)

        printed = " ".join(str(call) for call in mock_console.print.call_args_list)
        assert "access denied" in printed.lower()
        assert "401" in printed

    @patch("luthien_cli.repo.httpx.get")
    @patch("luthien_cli.repo.Console")
    def test_download_404_shows_generic_error(self, mock_console_cls, mock_get, tmp_path):
        """HTTP 404 should show the generic error (not access denied)."""
        import httpx

        mock_console = MagicMock()
        mock_console_cls.return_value = mock_console

        response = httpx.Response(404, request=httpx.Request("GET", "https://example.com"))
        mock_get.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=response.request,
            response=response,
        )

        with pytest.raises(SystemExit):
            _download_files(tmp_path)

        printed = " ".join(str(call) for call in mock_console.print.call_args_list)
        assert "access denied" not in printed.lower()
        assert "404" in printed
