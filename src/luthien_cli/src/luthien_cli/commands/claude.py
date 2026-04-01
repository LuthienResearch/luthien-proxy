"""luthien claude -- launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil
import sys
import time

import click
from rich.console import Console

from luthien_cli.commands.up import ensure_gateway_up
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


def _flush_terminal() -> None:
    """Flush stdout/stderr and drain any stale input from the terminal.

    Rich and other ANSI-aware libraries query terminal capabilities via
    escape sequences.  Responses arrive on stdin asynchronously.  If a
    response is still in the kernel's input buffer when os.execvpe hands
    off to Claude Code, Ink (Claude Code's TUI) reads that stale data
    instead of the reply to its own terminal queries and hangs.
    """
    sys.stdout.flush()
    sys.stderr.flush()

    if sys.stdin.isatty():
        try:
            import termios

            termios.tcflush(sys.stdin.fileno(), termios.TCIOFLUSH)
        except (ImportError, termios.error, OSError):
            pass


def _exec_claude(gateway_url: str, extra_args: list[str] | None = None) -> None:
    """Replace the current process with Claude Code.

    IMPORTANT: This function must not use Rich, click, or any library
    that emits ANSI terminal-capability queries.  Any such query
    triggers an asynchronous response on stdin that can corrupt Claude
    Code's Ink TUI initialisation — causing the terminal to freeze in
    raw mode.

    Only plain print() + sys.stdout.flush() are safe here.  The caller
    is responsible for completing all Rich output *before* invoking this
    function — ideally with human-scale delay (e.g. a keypress) between
    the last Rich call and this function so all async terminal responses
    have time to arrive and be drained by _flush_terminal().
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        print("Error: Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code")
        raise SystemExit(1)

    url = gateway_url.rstrip("/") + "/"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = url
    env.pop("ANTHROPIC_API_KEY", None)

    # Drain all ANSI responses that accumulated from prior Rich output.
    # A short sleep gives the terminal time to deliver any responses
    # that are still in flight from the last Rich call.
    time.sleep(0.05)
    _flush_terminal()

    print(f"Routing through {gateway_url} (OAuth passthrough)")
    print("Tip: run luthien commands with ! such as !luthien status, !luthien logs, !luthien up, and !luthien down")
    sys.stdout.flush()

    args = list(extra_args or [])
    os.execvpe("claude", ["claude", *args], env)


def _launch_claude(console: Console, extra_args: list[str] | None = None) -> None:
    """Launch Claude Code through the configured gateway.

    Called by `luthien claude`.  Ensures the gateway is running (Rich
    output), then hands off to _exec_claude for the actual exec.
    """
    # Ensure the gateway is running before handing off to Claude Code.
    # ensure_gateway_up is idempotent — returns immediately if already healthy.
    ensure_gateway_up(console)
    config = load_config(DEFAULT_CONFIG_PATH)

    # ── No Rich output past this point. ──────────────────────────────
    _exec_claude(config.gateway_url, extra_args)


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
