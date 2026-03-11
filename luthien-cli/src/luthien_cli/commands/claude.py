"""luthien claude -- launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil

import click
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


@click.command(context_settings={"ignore_unknown_options": True})
@click.argument("claude_args", nargs=-1, type=click.UNPROCESSED)
def claude(claude_args: tuple[str, ...]):
    """Launch Claude Code routed through the configured gateway.

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

    console.print(f"[blue]Routing Claude Code through {config.gateway_url}[/blue]")

    os.execvpe("claude", ["claude", *claude_args], env)
