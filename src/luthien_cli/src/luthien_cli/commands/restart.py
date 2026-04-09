"""luthien restart -- stop and start the gateway."""

import click
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config
from luthien_cli.local_process import is_gateway_running, stop_gateway


@click.command()
def restart():
    """Restart the gateway (stop then start).

    Stops the running gateway and starts it again. Useful after editing
    policy files to pick up code changes.
    """
    from luthien_cli.commands.up import ensure_gateway_up

    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    if config.mode == "local" and config.repo_path:
        if is_gateway_running(config.repo_path):
            stop_gateway(config.repo_path, console=console)
    elif config.mode == "docker" and config.repo_path:
        import subprocess

        with console.status("Stopping containers..."):
            subprocess.run(
                ["docker", "compose", "down"],
                cwd=config.repo_path,
                capture_output=True,
                text=True,
            )

    ensure_gateway_up(console)
