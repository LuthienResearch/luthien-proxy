"""luthien up/down -- manage gateway lifecycle (local process or Docker)."""

from __future__ import annotations

import os
import subprocess
import time
from urllib.parse import urlparse

import click
import httpx
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config
from luthien_cli.local_process import (
    find_docker_ports,
    gateway_log_path,
    is_gateway_running,
    start_gateway,
    stop_gateway,
)
from luthien_cli.repo import ensure_gateway_venv, ensure_repo, resolve_proxy_ref


def wait_for_healthy(url: str, timeout: int = 60, console: Console | None = None) -> bool:
    """Poll gateway /health until it responds or timeout."""
    deadline = time.time() + timeout

    def _poll() -> bool:
        while time.time() < deadline:
            try:
                r = httpx.get(f"{url}/health", timeout=5.0)
                if r.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
                pass
            time.sleep(2)
        return False

    if console is not None:
        with console.status("Waiting for gateway to be healthy..."):
            return _poll()
    return _poll()


def _port_from_url(url: str) -> int:
    """Extract port from a gateway URL, defaulting to 8000."""
    parsed = urlparse(url)
    return parsed.port or 8000


def ensure_gateway_up(
    console: Console,
    proxy_ref: str | None = None,
    *,
    force_reinstall: bool = False,
) -> None:
    """Ensure the gateway is running and healthy (idempotent).

    Returns immediately if the gateway is already healthy. Otherwise starts
    it using the configured mode (local vs docker) and waits for healthy.
    Raises SystemExit on failure.
    """
    config = load_config(DEFAULT_CONFIG_PATH)

    if is_gateway_healthy(config.gateway_url):
        console.print(f"[green]Gateway is healthy at {config.gateway_url}[/green]")
        return

    if config.mode == "local":
        if not config.repo_path or proxy_ref or force_reinstall:
            config.repo_path = ensure_gateway_venv(proxy_ref=proxy_ref, force_reinstall=force_reinstall)
            save_config(config, DEFAULT_CONFIG_PATH)

        console.print("[blue]Starting gateway (local mode)...[/blue]")

        existing = is_gateway_running(config.repo_path)
        if existing:
            console.print(f"[yellow]Gateway already running (PID {existing})[/yellow]")
        else:
            port = _port_from_url(config.gateway_url)
            pid = start_gateway(config.repo_path, port=port, console=console)
            console.print(f"[dim]Gateway started (PID {pid})[/dim]")

        if wait_for_healthy(config.gateway_url, console=console):
            console.print(f"[green]Gateway is healthy at {config.gateway_url}[/green]")
        else:
            console.print("[red]Gateway did not become healthy within 60s[/red]")
            console.print("[dim]Check logs: luthien logs[/dim]")
            raise SystemExit(1)

    else:
        if not config.repo_path:
            config.repo_path = ensure_repo()
            save_config(config, DEFAULT_CONFIG_PATH)

        console.print(f"[blue]Starting stack in {config.repo_path}[/blue]")

        gateway_running = subprocess.run(
            ["docker", "compose", "ps", "--services", "--filter", "status=running"],
            cwd=config.repo_path,
            capture_output=True,
            text=True,
        ).stdout.splitlines()

        port_env: dict[str, str] = {}
        if "gateway" in gateway_running:
            console.print("[yellow]Gateway already running.[/yellow]")
        else:
            port_env = find_docker_ports()
            if port_env:
                selected = ", ".join(f"{k}={v}" for k, v in port_env.items())
                console.print(f"[dim]Auto-selected ports: {selected}[/dim]")

        with console.status("Starting containers..."):
            result = subprocess.run(
                ["docker", "compose", "up", "-d"],
                cwd=config.repo_path,
                capture_output=True,
                text=True,
                env={**os.environ, **port_env},
            )
        if result.returncode != 0:
            console.print(f"[red]docker compose up failed:[/red]\n{result.stderr}")
            raise SystemExit(1)

        new_gateway_port = port_env.get(
            "GATEWAY_PORT", os.environ.get("GATEWAY_PORT", str(_port_from_url(config.gateway_url)))
        )
        new_gateway_url = f"http://localhost:{new_gateway_port}"
        if new_gateway_url != config.gateway_url:
            config.gateway_url = new_gateway_url
            save_config(config, DEFAULT_CONFIG_PATH)

        if wait_for_healthy(config.gateway_url, console=console):
            console.print(f"[green]Gateway is healthy at {config.gateway_url}[/green]")
        else:
            console.print("[red]Gateway did not become healthy within 60s[/red]")
            raise SystemExit(1)


def is_gateway_healthy(url: str) -> bool:
    """Quick one-shot health check (no retries)."""
    try:
        r = httpx.get(f"{url}/health", timeout=3.0)
        return r.status_code == 200
    except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
        return False


@click.command()
@click.option("--follow", "-f", is_flag=True, help="Tail gateway logs after startup")
@click.option("--proxy-ref", default=None, help="Git ref (branch, commit, or #PR) of luthien-proxy to install")
@click.option("--latest", is_flag=True, help="Force re-fetch of latest luthien-proxy from GitHub")
def up(follow: bool, proxy_ref: str | None, latest: bool):
    """Start the gateway (auto-detects local or Docker mode)."""
    console = Console()

    if proxy_ref or latest:
        config = load_config(DEFAULT_CONFIG_PATH)
        if config.mode == "docker":
            flag = "--proxy-ref" if proxy_ref else "--latest"
            console.print(f"[red]{flag} is not supported with Docker mode.[/red]")
            raise SystemExit(1)
        if proxy_ref:
            proxy_ref = resolve_proxy_ref(proxy_ref)

    ensure_gateway_up(console, proxy_ref=proxy_ref, force_reinstall=latest)

    if follow:
        config = load_config(DEFAULT_CONFIG_PATH)
        if config.mode == "local" and config.repo_path:
            log_path = gateway_log_path(config.repo_path)
            if log_path.exists():
                subprocess.run(["tail", "-f", str(log_path)])
        elif config.repo_path:
            subprocess.run(
                ["docker", "compose", "logs", "-f", "gateway"],
                cwd=config.repo_path,
            )


@click.command()
def down():
    """Stop the gateway (auto-detects local or Docker mode)."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.repo_path:
        console.print("[red]No repo_path configured. Nothing to stop.[/red]")
        raise SystemExit(1)

    if config.mode == "local":
        stop_gateway(config.repo_path, console=console)
    else:
        console.print(f"[blue]Stopping stack in {config.repo_path}[/blue]")

        with console.status("Stopping containers..."):
            result = subprocess.run(
                ["docker", "compose", "down"],
                cwd=config.repo_path,
                capture_output=True,
                text=True,
            )
        if result.returncode != 0:
            console.print(f"[red]docker compose down failed:[/red]\n{result.stderr}")
            raise SystemExit(1)

        console.print("[green]Stack stopped.[/green]")
