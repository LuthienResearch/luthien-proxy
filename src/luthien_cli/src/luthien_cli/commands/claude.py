"""luthien claude -- launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil
import webbrowser

import click
from rich.console import Console

from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config

ONBOARDING_PROMPT = (
    "I just installed luthien proxy! It's a proxy server that makes it easy to hack "
    "on the raw API data between Claude Code and the Anthropic backend before it even "
    "touches Claude Code, giving me more fine-grained control. Please give a short "
    "response - the proxy will take your response and modify it to include information "
    "about my luthien proxy install. This is the default onboarding policy and will "
    "only affect the first response - but I may activate other policies later on."
)


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

    # Open the config page in the browser
    config_url = config.gateway_url.rstrip("/") + "/policy-config"
    console.print(f"[dim]Opening config page: {config_url}[/dim]")
    webbrowser.open(config_url)

    console.print(
        "[dim]Tip: run luthien commands with ! such as !luthien status, !luthien logs, !luthien up, and !luthien down[/dim]"
    )

    # Pre-seed the first message with onboarding prompt if no explicit prompt was given
    args = list(extra_args or [])
    has_prompt = "-p" in args or "--prompt" in args
    if not has_prompt:
        args = ["-p", ONBOARDING_PROMPT, *args]

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
