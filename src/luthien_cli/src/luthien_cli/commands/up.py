"""luthien up/down -- manage gateway lifecycle (local process or Docker)."""

from __future__ import annotations

import subprocess
import time

import click
import httpx
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config
from luthien_cli.local_process import is_gateway_running, start_gateway, stop_gateway
from luthien_cli.repo import ensure_gateway_venv, ensure_repo


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


@click.command()
@click.option("--follow", "-f", is_flag=True, help="Tail gateway logs after startup")
def up(follow: bool):
    """Start the gateway (auto-detects local or Docker mode)."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if config.mode == "local":
        if not config.repo_path:
            config.repo_path = ensure_gateway_venv()
            save_config(config, DEFAULT_CONFIG_PATH)

        console.print("[blue]Starting gateway (local mode)...[/blue]")

        existing = is_gateway_running(config.repo_path)
        if existing:
            console.print(f"[yellow]Gateway already running (PID {existing})[/yellow]")
        else:
            port = int(config.gateway_url.rsplit(":", 1)[-1]) if ":" in config.gateway_url else 8000
            pid = start_gateway(config.repo_path, port=port, console=console)
            console.print(f"[dim]Gateway started (PID {pid})[/dim]")

        if wait_for_healthy(config.gateway_url, console=console):
            console.print(f"[green]Gateway is healthy at {config.gateway_url}[/green]")
        else:
            console.print("[red]Gateway did not become healthy within 60s[/red]")
            console.print("[dim]Check logs: luthien logs[/dim]")
            raise SystemExit(1)

        if follow:
            from luthien_cli.local_process import gateway_log_path

            log_path = gateway_log_path(config.repo_path)
            if log_path.exists():
                subprocess.run(["tail", "-f", str(log_path)])

    else:
        if not config.repo_path:
            config.repo_path = ensure_repo()
            save_config(config, DEFAULT_CONFIG_PATH)

        console.print(f"[blue]Starting stack in {config.repo_path}[/blue]")

        with console.status("Starting containers..."):
            result = subprocess.run(
                ["docker", "compose", "up", "-d"],
                cwd=config.repo_path,
                capture_output=True,
                text=True,
            )
        if result.returncode != 0:
            console.print(f"[red]docker compose up failed:[/red]\n{result.stderr}")
            raise SystemExit(1)

        if wait_for_healthy(config.gateway_url, console=console):
            console.print(f"[green]Gateway is healthy at {config.gateway_url}[/green]")
        else:
            console.print("[red]Gateway did not become healthy within 60s[/red]")
            raise SystemExit(1)

        if follow:
            subprocess.run(
                ["docker", "compose", "logs", "-f", "gateway"],
                cwd=config.repo_path,
            )


@click.command()
def down():
    """Stop the gateway (auto-detects local or Docker mode)."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if config.mode == "local":
        if not config.repo_path:
            console.print("[red]No repo_path configured. Nothing to stop.[/red]")
            raise SystemExit(1)

        stop_gateway(config.repo_path, console=console)

    else:
        if not config.repo_path:
            console.print("[red]No repo_path configured. Nothing to stop.[/red]")
            raise SystemExit(1)

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
