"""luthien status -- show gateway state."""

import click
from rich.console import Console
from rich.table import Table

from luthien_cli.config import load_config
from luthien_cli.gateway_client import GatewayClient, GatewayError


def make_client() -> GatewayClient:
    config = load_config()
    return GatewayClient(
        base_url=config.gateway_url,
        admin_key=config.admin_key,
    )


@click.command()
def status():
    """Show gateway health, active policy, and auth mode."""
    console = Console()
    client = make_client()

    try:
        health = client.health()
    except GatewayError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    table = Table(title="Gateway Status")
    table.add_column("Property", style="bold")
    table.add_column("Value")

    table.add_row("URL", client.base_url)
    table.add_row("Status", f"[green]{health['status']}[/green]")
    table.add_row("Version", health.get("version", "unknown"))

    try:
        policy = client.get_current_policy()
        table.add_row("Policy", policy["policy"])
        table.add_row("Policy Class", policy["class_ref"])
    except GatewayError:
        table.add_row("Policy", "[yellow]unavailable (no admin key?)[/yellow]")

    try:
        auth = client.get_auth_config()
        table.add_row("Auth Mode", auth["auth_mode"])
    except GatewayError:
        table.add_row("Auth Mode", "[yellow]unavailable[/yellow]")

    console.print(table)
