"""luthien restart -- stop and start the gateway."""

import subprocess

import click
from rich.console import Console

from luthien_cli.commands.up import ensure_gateway_up
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config
from luthien_cli.local_process import is_gateway_running, stop_gateway


@click.command()
def restart():
    """Restart the gateway (stop then start).

    Stops the running gateway and starts it again. Useful after editing
    policy files to pick up code changes.
    """
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.repo_path:
        console.print("[red]No repo_path configured. Run `luthien onboard` first.[/red]")
        raise SystemExit(1)

    if config.mode == "local":
        if is_gateway_running(config.repo_path):
            stop_gateway(config.repo_path, console=console)
        else:
            console.print("[dim]No running gateway found — starting fresh.[/dim]")
    elif config.mode == "docker":
        try:
            with console.status("Stopping containers..."):
                result = subprocess.run(
                    ["docker", "compose", "down"],
                    cwd=config.repo_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            if result.returncode != 0:
                console.print(f"[yellow]Warning: docker compose down failed:[/yellow]\n{result.stderr}")
        except subprocess.TimeoutExpired:
            console.print("[yellow]Warning: docker compose down timed out after 60s[/yellow]")
    else:
        console.print(f"[red]Unknown mode: {config.mode}[/red]")
        raise SystemExit(1)

    ensure_gateway_up(console)
