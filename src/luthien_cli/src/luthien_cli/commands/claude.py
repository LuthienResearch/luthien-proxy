"""luthien claude -- launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil
import sys

import click
from rich.console import Console

from luthien_cli.commands.up import ensure_gateway_up
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


def _ensure_tty_stdin() -> None:
    """Reopen /dev/tty as fd 0 so the exec'd process gets a real TTY.

    When the install script runs via ``curl | bash``, bash's stdin is a
    pipe.  The script redirects with ``</dev/tty``, but the resulting fd
    may be O_RDONLY or may have been disturbed by Python's buffered-IO
    layer during the onboard flow.  Node.js (Claude Code) checks
    ``process.stdin.isTTY``; if that is false, Ink's TUI cannot
    initialise and the terminal freezes in raw mode.

    Opening ``/dev/tty`` with O_RDWR and dup2-ing it onto fd 0
    guarantees the next process inherits a fully functional TTY on
    stdin — regardless of how this process was launched.
    """
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
        if tty_fd != 0:
            os.dup2(tty_fd, 0)
            os.close(tty_fd)
    except OSError:
        pass  # No controlling terminal — best-effort


def _flush_terminal() -> None:
    """Flush stdout/stderr and drain any stale input from the terminal."""
    sys.stdout.flush()
    sys.stderr.flush()

    try:
        import termios

        if os.isatty(0):
            termios.tcflush(0, termios.TCIOFLUSH)
    except (ImportError, termios.error, OSError):
        pass


def _exec_claude(gateway_url: str, extra_args: list[str] | None = None) -> None:
    """Replace the current process with Claude Code.

    IMPORTANT: This function must not use Rich, click, or any library
    that emits ANSI terminal-capability queries.  Only plain print() is
    safe here.
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        print("Error: Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code")
        raise SystemExit(1)

    url = gateway_url.rstrip("/") + "/"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = url
    env.pop("ANTHROPIC_API_KEY", None)

    # Ensure fd 0 is a real read-write TTY for Claude Code's Ink TUI.
    _ensure_tty_stdin()

    # Drain any stale bytes from prior Rich output / terminal queries.
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
    ensure_gateway_up(console)
    config = load_config(DEFAULT_CONFIG_PATH)
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
