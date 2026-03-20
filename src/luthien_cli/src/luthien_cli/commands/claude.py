"""luthien claude -- launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil

import click
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


@click.command(context_settings={"ignore_unknown_options": True})
@click.argument("claude_args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--api-key",
    envvar="LUTHIEN_API_KEY",
    default=None,
    help="Gateway proxy API key (overrides config). Default: use OAuth passthrough.",
)
def claude(claude_args: tuple[str, ...], api_key: str | None):
    """Launch Claude Code routed through the configured gateway.

    By default, uses OAuth passthrough — no API key needed.
    Your existing Claude Code authentication is forwarded through the gateway.

    All arguments after 'claude' are passed through to Claude Code.
    """
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    claude_path = shutil.which("claude")
    if not claude_path:
        console.print("[red]Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-cli[/red]")
        raise SystemExit(1)

    gateway_url = config.gateway_url.rstrip("/") + "/"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = gateway_url

    effective_key = api_key or config.api_key
    if effective_key:
        env["ANTHROPIC_API_KEY"] = effective_key
        console.print(f"[blue]Routing through {config.gateway_url} (proxy API key)[/blue]")
    else:
        # Remove any inherited API key so Claude Code doesn't warn about
        # "both OAuth token and API key" being set simultaneously.
        env.pop("ANTHROPIC_API_KEY", None)
        console.print(f"[blue]Routing through {config.gateway_url} (OAuth passthrough)[/blue]")

    console.print(
        "[dim]Tip: run luthien commands with ! such as !luthien status, !luthien logs, !luthien up, and !luthien down[/dim]"
    )

    os.execvpe("claude", ["claude", *claude_args], env)
