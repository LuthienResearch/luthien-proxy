"""luthien claude -- launch Claude Code through the gateway."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import click
from rich.console import Console

from luthien_cli.commands.up import ensure_gateway_up
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config


def _exec_claude(gateway_url: str, extra_args: list[str] | None = None) -> None:
    """Launch Claude Code as a child process with a clean TTY.

    Previous attempts used os.execvpe (replace-in-place), but the
    inherited process state from the onboard flow (curl|bash pipe,
    Python's buffered IO, Rich's terminal queries) left stdin in a
    condition where Node.js saw process.stdin.isTTY === false, causing
    Ink's TUI to freeze.

    Spawning Claude Code as a child process via subprocess.run gives it
    fresh stdio file descriptors.  Opening /dev/tty explicitly as stdin
    guarantees a real read-write TTY regardless of how the parent was
    launched (pipe, redirect, etc.).
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        print("Error: Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-code")
        raise SystemExit(1)

    url = gateway_url.rstrip("/") + "/"

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = url
    env.pop("ANTHROPIC_API_KEY", None)

    args = ["claude", *(extra_args or [])]

    # Open /dev/tty directly as stdin for Claude Code.  This gives it a
    # real O_RDWR TTY fd — no matter what happened to this process's
    # fd 0 during the onboard flow (pipe from curl, Python buffered IO,
    # Rich terminal queries, raw-mode toggle, etc.).
    tty_fd: int | None = None
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
    except OSError as exc:
        print(f"[debug] could not open /dev/tty: {exc}")

    # Diagnostic output — visible in the terminal before Claude launches.
    # If the launch still freezes, ask the tester to screenshot/copy this.
    stdin_fd = tty_fd if tty_fd is not None else sys.stdin.fileno()
    print(f"Routing through {gateway_url} (OAuth passthrough)")
    print("Tip: run luthien commands with ! such as !luthien status, !luthien logs, !luthien up, and !luthien down")
    print(f"[debug] claude binary: {shutil.which('claude')}")
    print(f"[debug] tty_fd={tty_fd}, stdin_fd={stdin_fd}, isatty(stdin_fd)={os.isatty(stdin_fd)}")
    print(f"[debug] fd0 isatty={os.isatty(0)}, fd1 isatty={os.isatty(1)}, fd2 isatty={os.isatty(2)}")
    try:
        print(f"[debug] /dev/tty open mode: O_RDWR, fd={tty_fd}")
        if tty_fd is not None:
            import stat

            st = os.fstat(tty_fd)
            print(f"[debug] tty_fd fstat: mode={oct(st.st_mode)}, is_chr={stat.S_ISCHR(st.st_mode)}")
    except Exception as exc:
        print(f"[debug] fstat error: {exc}")
    sys.stdout.flush()

    try:
        result = subprocess.run(
            args,
            env=env,
            stdin=tty_fd if tty_fd is not None else sys.stdin,
        )
    finally:
        if tty_fd is not None:
            os.close(tty_fd)

    raise SystemExit(result.returncode)


def _launch_claude(console: Console, extra_args: list[str] | None = None) -> None:
    """Launch Claude Code through the configured gateway.

    Called by `luthien claude`.  Ensures the gateway is running (Rich
    output), then hands off to _exec_claude for the actual launch.
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
