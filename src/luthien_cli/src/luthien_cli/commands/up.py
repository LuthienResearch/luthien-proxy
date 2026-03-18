"""luthien up/down -- manage local docker-compose stack."""

from __future__ import annotations

import subprocess
import time

import click
import httpx
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config
from luthien_cli.repo import ensure_repo


def wait_for_healthy(url: str, timeout: int = 60) -> bool:
    """Poll gateway /health until it responds or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/health", timeout=5.0)
            if r.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
            pass
        time.sleep(2)
    return False


@click.command()
@click.option("--follow", "-f", is_flag=True, help="Tail gateway logs after startup")
def up(follow: bool):
    """Start the local luthien-proxy stack (db, redis, gateway)."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.repo_path:
        config.repo_path = ensure_repo()
        save_config(config, DEFAULT_CONFIG_PATH)

    console.print(f"[blue]Starting stack in {config.repo_path}[/blue]")

    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=config.repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]docker compose up failed:[/red]\n{result.stderr}")
        raise SystemExit(1)

    console.print("[yellow]Waiting for gateway to be healthy...[/yellow]")
    if wait_for_healthy(config.gateway_url):
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
    """Stop the local luthien-proxy stack."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.repo_path:
        console.print("[red]No repo_path configured. Nothing to stop.[/red]")
        raise SystemExit(1)

    console.print(f"[blue]Stopping stack in {config.repo_path}[/blue]")

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
