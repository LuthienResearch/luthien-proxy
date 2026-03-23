"""Tests for scripts/auth_mode_check.sh bash functions."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts")
AUTH_MODE_SCRIPT = os.path.join(SCRIPT_DIR, "auth_mode_check.sh")


def run_bash(code: str, stdin: str | None = None, timeout: int = 5) -> subprocess.CompletedProcess[str]:
    """Source auth_mode_check.sh and run bash code."""
    full_code = f"source {AUTH_MODE_SCRIPT}\n{code}"
    return subprocess.run(
        ["bash", "-c", full_code],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestCheckAuthModeInteractive:
    """Tests for the check_auth_mode_interactive function."""

    def test_skips_when_mode_is_both(self) -> None:
        result = run_bash('check_auth_mode_interactive "both"')
        assert result.stdout.strip() == "both"
        assert result.returncode == 1

    def test_skips_when_mode_is_passthrough(self) -> None:
        result = run_bash('check_auth_mode_interactive "passthrough"')
        assert result.stdout.strip() == "passthrough"
        assert result.returncode == 1

    def test_skips_in_noninteractive_mode(self) -> None:
        """When stdin is not a TTY (piped), should skip silently."""
        result = run_bash('check_auth_mode_interactive "proxy_key"')
        assert result.stdout.strip() == "proxy_key"
        assert result.returncode == 1


class TestUpdateAuthModeEnv:
    """Tests for the update_auth_mode_env function."""

    def test_updates_existing_auth_mode(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("AUTH_MODE=proxy_key\nOTHER=value\n")

        result = run_bash(f'update_auth_mode_env "both" "{env_file}"')
        assert result.returncode == 0

        content = env_file.read_text()
        assert "AUTH_MODE=both" in content
        assert "OTHER=value" in content

    def test_updates_commented_auth_mode(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("# AUTH_MODE=proxy_key\nOTHER=value\n")

        result = run_bash(f'update_auth_mode_env "passthrough" "{env_file}"')
        assert result.returncode == 0

        content = env_file.read_text()
        assert "AUTH_MODE=passthrough" in content

    def test_appends_when_missing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=value\n")

        result = run_bash(f'update_auth_mode_env "both" "{env_file}"')
        assert result.returncode == 0

        content = env_file.read_text()
        assert "AUTH_MODE=both" in content
        assert "OTHER=value" in content

    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"

        result = run_bash(f'update_auth_mode_env "both" "{env_file}"')
        assert result.returncode == 0

        content = env_file.read_text()
        assert "AUTH_MODE=both" in content

    def test_preserves_other_lines(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("PROXY_API_KEY=sk-test\nAUTH_MODE=proxy_key\nDATABASE_URL=postgres://localhost\n")

        run_bash(f'update_auth_mode_env "both" "{env_file}"')

        content = env_file.read_text()
        assert "PROXY_API_KEY=sk-test" in content
        assert "AUTH_MODE=both" in content
        assert "DATABASE_URL=postgres://localhost" in content
