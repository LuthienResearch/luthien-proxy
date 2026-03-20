"""luthien claude -- launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil

import click
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


def _launch_claude(console: Console, extra_args: list[str] | None = None) -> None:
    """Launch Claude Code through the configured gateway.

    Called by both `luthien claude` and `luthien onboard` (after setup).
    """
    config = load_config(DEFAULT_CONFIG_PATH)

    claude_path = shutil.which("claude")
    if not claude_path:
        console.print("[red]Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-cli[/red]")
        raise SystemExit(1)

    gateway_url = config.gateway_url.rstrip("/") + "/"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = gateway_url

    # Always remove any inherited API key so Claude Code uses OAuth
    # passthrough without warning about conflicting credentials.
    env.pop("ANTHROPIC_API_KEY", None)
    console.print(f"[blue]Routing through {config.gateway_url} (OAuth passthrough)[/blue]")

    console.print(
        "[dim]Tip: run luthien commands with ! such as !luthien status, !luthien logs, !luthien up, and !luthien down[/dim]"
    )

    args = list(extra_args or [])
    os.execvpe("claude", ["claude", *args], env)


@click.command(context_settings={"ignore_unknown_options": True})
@click.argument("claude_args", nargs=-1, type=click.UNPROCESSED)
def claude(claude_args: tuple[str, ...]):
    """Launch Claude Code routed through the configured gateway.

    Uses OAuth passthrough — your existing Claude Code authentication
    is forwarded through the gateway. No API key needed.

    All arguments after 'claude' are passed through to Claude Code.
    """
    console = Console()
    _launch_claude(console, list(claude_args))
