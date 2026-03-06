"""luthien logs -- view gateway logs."""

from __future__ import annotations

import subprocess

import click
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


@click.command()
@click.option("--tail", "-n", default=None, type=int, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
def logs(tail: int | None, follow: bool):
    """View gateway logs (requires local repo_path configured)."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if not config.repo_path:
        console.print("[red]No repo_path configured. Set it with: luthien config set local.repo_path <path>[/red]")
        raise SystemExit(1)

    cmd = ["docker", "compose", "logs", "gateway"]
    if tail is not None:
        cmd.extend(["--tail", str(tail)])
    if follow:
        cmd.append("-f")

    subprocess.run(cmd, cwd=config.repo_path)
