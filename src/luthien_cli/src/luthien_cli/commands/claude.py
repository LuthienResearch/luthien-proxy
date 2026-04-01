"""luthien claude -- launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil
import sys

import click
from rich.console import Console

from luthien_cli.commands.up import ensure_gateway_up
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


def _exec_claude(gateway_url: str, extra_args: list[str] | None = None) -> None:
    """Replace the current process with Claude Code.

    Uses os.execvpe so Claude Code inherits this process's PID,
    process group, and session — guaranteeing it is the terminal's
    foreground process and can read/write the TTY.
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        print("Error: Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code")
        raise SystemExit(1)

    url = gateway_url.rstrip("/") + "/"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = url
    env.pop("ANTHROPIC_API_KEY", None)

    print(f"Routing through {gateway_url} (OAuth passthrough)")
    print("Tip: run luthien commands with ! such as !luthien status, !luthien logs, !luthien up, and !luthien down")
    sys.stdout.flush()

    # When launched via `curl | bash`, the shell redirect `</dev/tty`
    # opens /dev/tty as O_RDONLY for fd 0.  Bun's kevent-based input
    # polling needs a read-write fd.  Reopen /dev/tty as O_RDWR and
    # replace only fd 0 — leave fd 1/2 alone since they are already
    # proper pty fds and dup2'ing /dev/tty onto them breaks Bun's
    # kqueue (EINVAL on the indirect /dev/tty device node).
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
        if tty_fd != 0:
            os.dup2(tty_fd, 0)
            os.close(tty_fd)
    except OSError:
        pass  # not a terminal — best effort

    args = ["claude", *(extra_args or [])]
    os.execvpe("claude", args, env)


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
