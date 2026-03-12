"""luthien config -- view and edit CLI configuration."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from luthien_cli.config import DEFAULT_CONFIG_PATH, SECRET_KEYS, load_config, save_config

FIELD_MAP = {
    "gateway.url": "gateway_url",
    "gateway.api_key": "api_key",
    "gateway.admin_key": "admin_key",
    "local.repo_path": "repo_path",
}


@click.group()
def config():
    """View or edit luthien CLI configuration."""


@config.command()
def show():
    """Display current configuration."""
    console = Console()
    cfg = load_config(DEFAULT_CONFIG_PATH)

    table = Table(title=f"Config ({DEFAULT_CONFIG_PATH})")
    table.add_column("Key", style="bold")
    table.add_column("Value")

    table.add_row("gateway.url", cfg.gateway_url)
    table.add_row("gateway.api_key", _mask(cfg.api_key))
    table.add_row("gateway.admin_key", _mask(cfg.admin_key))
    table.add_row("local.repo_path", cfg.repo_path or "[dim]not set[/dim]")

    console.print(table)


@config.command("set")
@click.argument("key")
@click.argument("value")
def set_value(key: str, value: str):
    """Set a config value. Keys: gateway.url, gateway.api_key, gateway.admin_key, local.repo_path."""
    console = Console()
    cfg = load_config(DEFAULT_CONFIG_PATH)

    if key not in FIELD_MAP:
        console.print(f"[red]Unknown key: {key}[/red]")
        console.print(f"Valid keys: {', '.join(FIELD_MAP.keys())}")
        raise SystemExit(1)

    setattr(cfg, FIELD_MAP[key], value)
    save_config(cfg, DEFAULT_CONFIG_PATH)

    display_value = _mask(value) if FIELD_MAP[key] in SECRET_KEYS else value
    console.print(f"[green]Set {key} = {display_value}[/green]")


def _mask(value: str | None) -> str:
    if not value:
        return "[dim]not set[/dim]"
    if len(value) <= 8:
        return "****"
    return value[:4] + "..." + value[-4:]
