"""luthien logs -- view gateway logs."""

from __future__ import annotations

import subprocess

import click
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config
from luthien_cli.local_process import gateway_log_path


@click.command()
@click.option("--tail", "-n", default=None, type=int, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
def logs(tail: int | None, follow: bool):
    """View gateway logs."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.repo_path:
        console.print("[red]No repo_path configured. Run 'luthien onboard' first.[/red]")
        raise SystemExit(1)

    if config.mode == "local":
        log_path = gateway_log_path(config.repo_path)
        if not log_path.exists():
            console.print("[yellow]No log file found. Has the gateway been started?[/yellow]")
            raise SystemExit(1)

        cmd = ["tail"]
        if tail is not None:
            cmd.extend(["-n", str(tail)])
        if follow:
            cmd.append("-f")
        cmd.append(str(log_path))
        subprocess.run(cmd)

    else:
        cmd = ["docker", "compose", "logs", "gateway"]
        if tail is not None:
            cmd.extend(["--tail", str(tail)])
        if follow:
            cmd.append("-f")
        subprocess.run(cmd, cwd=config.repo_path)
