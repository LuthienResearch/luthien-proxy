"""Manage the gateway as a local background process (no Docker)."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console

from luthien_cli.repo import MANAGED_VENV_DIR

GATEWAY_PID_FILE = "gateway.pid"
GATEWAY_LOG_FILE = "gateway.log"


def _pid_file(repo_path: str) -> Path:
    return Path(repo_path) / GATEWAY_PID_FILE


def _log_file(repo_path: str) -> Path:
    return Path(repo_path) / GATEWAY_LOG_FILE


def _venv_python() -> str:
    """Path to the Python interpreter in the managed venv."""
    return str(MANAGED_VENV_DIR / "bin" / "python")


def _parse_env_value(value: str) -> str:
    """Strip surrounding quotes from a .env value."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _is_unix() -> bool:
    return sys.platform != "win32"


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
    if not _is_unix():
        raise RuntimeError("Local mode requires Unix (Linux/macOS). Use 'luthien onboard --docker' on Windows.")

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
                env[key.strip()] = _parse_env_value(value.strip())

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

    try:
        _pid_file(repo_path).write_text(str(proc.pid))
    except Exception:
        proc.terminate()
        raise

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

    # Wait briefly for the process to exit, escalate to SIGKILL if needed
    for _ in range(10):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.3)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    _pid_file(repo_path).unlink(missing_ok=True)

    if console:
        console.print(f"[green]Gateway stopped (PID {pid}).[/green]")
    return True


def gateway_log_path(repo_path: str) -> Path:
    """Return the path to the gateway log file."""
    return _log_file(repo_path)


_DOCKER_PORT_DEFAULTS: dict[str, int] = {
    "POSTGRES_PORT": 5433,
    "REDIS_PORT": 6379,
    "GATEWAY_PORT": 8000,
}


def is_port_free(port: int) -> bool:
    """Check if a TCP port is available on localhost."""
    if not 1024 <= port <= 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port(start: int) -> int:
    """Find the next free port starting from the given default."""
    for offset in range(100):
        port = start + offset
        if is_port_free(port):
            return port
    raise RuntimeError(f"Could not find a free port starting from {start}")


def find_docker_ports() -> dict[str, str]:
    """Auto-select free ports for docker compose services."""
    port_env: dict[str, str] = {}
    for var, default in _DOCKER_PORT_DEFAULTS.items():
        if os.environ.get(var):
            continue
        port = find_free_port(default)
        port_env[var] = str(port)
    return port_env
