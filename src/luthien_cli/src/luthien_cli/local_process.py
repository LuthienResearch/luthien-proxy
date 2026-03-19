"""Manage the gateway as a local background process (no Docker)."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from rich.console import Console

GATEWAY_PID_FILE = "gateway.pid"
GATEWAY_LOG_FILE = "gateway.log"


def _pid_file(repo_path: str) -> Path:
    return Path(repo_path) / GATEWAY_PID_FILE


def _log_file(repo_path: str) -> Path:
    return Path(repo_path) / GATEWAY_LOG_FILE


def _venv_python() -> str:
    """Path to the Python interpreter in the managed venv."""
    return str(Path.home() / ".luthien" / "venv" / "bin" / "python")


def is_gateway_running(repo_path: str) -> int | None:
    """Check if gateway is running. Returns PID if alive, None otherwise."""
    pid_path = _pid_file(repo_path)
    if not pid_path.exists():
        return None

    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return None

    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        pid_path.unlink(missing_ok=True)
        return None


def start_gateway(
    repo_path: str,
    port: int = 8000,
    console: Console | None = None,
) -> int:
    """Start the gateway as a detached background process.

    Returns the PID of the started process.
    """
    existing_pid = is_gateway_running(repo_path)
    if existing_pid is not None:
        if console:
            console.print(f"[yellow]Gateway already running (PID {existing_pid})[/yellow]")
        return existing_pid

    python = _venv_python()
    if not Path(python).exists():
        raise RuntimeError(f"Gateway venv not found at {python}. Run 'luthien onboard' first.")

    env_file = Path(repo_path) / ".env"
    env = os.environ.copy()

    # Load .env file into environment
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()

    env["GATEWAY_PORT"] = str(port)

    log_path = _log_file(repo_path)
    log_handle = open(log_path, "a")

    try:
        proc = subprocess.Popen(
            [python, "-m", "luthien_proxy.main"],
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            env=env,
            cwd=repo_path,
        )
    except Exception:
        log_handle.close()
        raise

    # Detached process inherits the file handle; parent can close its copy
    log_handle.close()

    _pid_file(repo_path).write_text(str(proc.pid))
    return proc.pid


def stop_gateway(repo_path: str, console: Console | None = None) -> bool:
    """Stop the gateway background process. Returns True if it was running."""
    pid = is_gateway_running(repo_path)
    if pid is None:
        if console:
            console.print("[yellow]Gateway is not running.[/yellow]")
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass

    _pid_file(repo_path).unlink(missing_ok=True)

    if console:
        console.print(f"[green]Gateway stopped (PID {pid}).[/green]")
    return True


def gateway_log_path(repo_path: str) -> Path:
    """Return the path to the gateway log file."""
    return _log_file(repo_path)
