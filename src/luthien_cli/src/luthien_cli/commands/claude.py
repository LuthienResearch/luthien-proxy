"""luthien claude -- launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil
import sys

import click
from rich.console import Console

from luthien_cli.commands.up import ensure_gateway_up
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


def _flush_terminal() -> None:
    """Flush stdout/stderr and drain any stale input from the terminal.

    Rich and other ANSI-aware libraries query terminal capabilities via
    escape sequences. Responses arrive on stdin asynchronously. If a
    response is still in the kernel's input buffer when os.execvpe hands
    off to Claude Code, Ink (Claude Code's TUI) reads that stale data
    instead of the reply to its own terminal queries and hangs.

    Flushing both directions ensures Claude Code inherits a clean
    terminal with no pending I/O.
    """
    sys.stdout.flush()
    sys.stderr.flush()

    if sys.stdin.isatty():
        try:
            import termios

            termios.tcflush(sys.stdin.fileno(), termios.TCIOFLUSH)
        except (ImportError, termios.error, OSError):
            pass


def _launch_claude(console: Console, extra_args: list[str] | None = None) -> None:
    """Launch Claude Code through the configured gateway.

    Called by both `luthien claude` and `luthien onboard` (after setup).
    Automatically starts the gateway if it isn't running.
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        console.print("[red]Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code[/red]")
        raise SystemExit(1)

    # Ensure the gateway is running before handing off to Claude Code.
    # ensure_gateway_up is idempotent — returns immediately if already healthy.
    ensure_gateway_up(console)
    config = load_config(DEFAULT_CONFIG_PATH)

    gateway_url = config.gateway_url.rstrip("/") + "/"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = gateway_url

    # Always remove any inherited API key so Claude Code uses OAuth
    # passthrough without warning about conflicting credentials.
    env.pop("ANTHROPIC_API_KEY", None)

    # ── No Rich output past this point. ──────────────────────────────
    # Rich emits ANSI escape sequences that query terminal capabilities.
    # The terminal sends responses back on stdin *asynchronously*.  If
    # any response is still in flight when os.execvpe hands control to
    # Claude Code, Ink reads the stale bytes instead of its own terminal
    # query replies and the TUI hangs in raw mode.
    #
    # _flush_terminal() drains what is in the kernel buffer NOW, but
    # cannot drain what hasn't arrived yet.  So we must ensure no ANSI-
    # emitting code runs between here and exec.  Use only plain print().
    _flush_terminal()

    print(f"Routing through {config.gateway_url} (OAuth passthrough)")
    print("Tip: run luthien commands with ! such as !luthien status, !luthien logs, !luthien up, and !luthien down")
    sys.stdout.flush()

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
