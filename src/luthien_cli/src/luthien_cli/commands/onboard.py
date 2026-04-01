"""luthien onboard -- set up and start a local gateway with the onboarding policy."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
import textwrap
import webbrowser
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from luthien_cli.commands.up import wait_for_healthy
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config
from luthien_cli.local_process import find_docker_ports, find_free_port, start_gateway, stop_gateway
from luthien_cli.repo import ensure_gateway_venv, ensure_repo, ensure_repo_clone, resolve_proxy_ref

# Substrings in Docker-pull stderr that indicate an authentication/authorization failure.
_GHCR_AUTH_HINTS = ("401", "403", "forbidden", "unauthorized", "access to the resource is denied")


def _read_single_key() -> str:
    """Read a single keypress without waiting for Enter (Unix/macOS only)."""
    if not sys.stdin.isatty():
        return sys.stdin.read(1) or "\n"

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        # Use os.read() — NOT sys.stdin.read().  sys.stdin is a
        # buffered TextIOWrapper whose underlying BufferedReader calls
        # os.read(fd, 8192), consuming ALL available bytes from the
        # kernel input buffer into Python's userspace buffer.  Only the
        # one requested character is returned; the rest are trapped.
        # After os.execvpe replaces this process, that buffer is
        # destroyed — the bytes are gone.  If they included terminal
        # escape-sequence responses, Claude Code's Ink TUI will never
        # see them, hang waiting for answers the terminal already sent,
        # and freeze the terminal in raw mode.
        #
        # os.read(fd, 1) is a raw syscall: exactly one byte is removed
        # from the kernel buffer.  Everything else stays for tcflush or
        # the next process to consume.
        ch = os.read(fd, 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch.decode("ascii", errors="replace")


ONBOARDING_PROMPT = (
    "I just installed luthien proxy! It's a proxy server that makes it easy to hack "
    "on the raw API data between Claude Code and the Anthropic backend before it even "
    "touches Claude Code, giving me more fine-grained control. Please give a short "
    "response - the proxy will take your response and modify it to include information "
    "about my luthien proxy install. This is the default onboarding policy and will "
    "only affect the first response - but I may activate other policies later on."
)

ONBOARDING_POLICY_CLASS = "luthien_proxy.policies.onboarding_policy:OnboardingPolicy"

_LOCAL_MODE_HINT = "\n[bold]Alternative:[/bold] Try local mode instead (no Docker required):\n  [green]luthien onboard[/green] (without [cyan]--docker[/cyan])"


def _generate_key(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


def _write_local_env(
    repo_path: str,
    admin_key: str,
    sentry_enabled: bool = False,
    sentry_dsn: str = "",
) -> None:
    """Write .env for local mode (SQLite, no Redis, no proxy key required)."""
    db_path = str(Path(repo_path) / "luthien.db")
    policy_path = str(Path(repo_path) / "config" / "policy_config.yaml")

    env_content = (
        f"DATABASE_URL=sqlite:///{db_path}\n"
        f"ADMIN_API_KEY={admin_key}\n"
        f"POLICY_SOURCE=file\n"
        f"POLICY_CONFIG={policy_path}\n"
        f"AUTH_MODE=both\n"
        f"OTEL_ENABLED=false\n"
        f"USAGE_TELEMETRY=true\n"
        f"SENTRY_ENABLED={str(sentry_enabled).lower()}\n"
    )
    if sentry_dsn:
        env_content += f"SENTRY_DSN={sentry_dsn}\n"

    env_path = f"{repo_path}/.env"
    with open(env_path, "w") as f:
        f.write(env_content)
    os.chmod(env_path, 0o600)


def _ensure_docker_env(
    repo_path: str,
    admin_key: str,
    sentry_enabled: bool = False,
    sentry_dsn: str = "",
) -> None:
    """Create or update .env with Docker onboard settings.

    Sets the admin key and Postgres/Redis connection vars that
    Docker Compose requires at pull/start time.
    """
    env_path = f"{repo_path}/.env"
    env_example = f"{repo_path}/.env.example"

    try:
        with open(env_path) as f:
            env_content = f.read()
    except FileNotFoundError:
        try:
            with open(env_example) as f:
                env_content = f.read()
        except FileNotFoundError:
            env_content = ""

    pg_password = secrets.token_urlsafe(16)
    overrides: dict[str, str] = {
        "ADMIN_API_KEY": admin_key,
        "AUTH_MODE": "both",
        "POLICY_SOURCE": "file",
        "POSTGRES_USER": "luthien",
        "POSTGRES_PASSWORD": pg_password,
        "POSTGRES_DB": "luthien_control",
        "POSTGRES_PORT": "5433",
        "DATABASE_URL": f"postgresql://luthien:{pg_password}@db:5432/luthien_control",
        "REDIS_URL": "redis://redis:6379",
        "REDIS_PORT": "6379",
        "SENTRY_ENABLED": str(sentry_enabled).lower(),
    }
    if sentry_dsn:
        overrides["SENTRY_DSN"] = sentry_dsn

    for key, value in overrides.items():
        pattern = rf"^#?\s*{key}=.*$"
        replacement = f"{key}={value}"
        new_content, count = re.subn(pattern, replacement, env_content, flags=re.MULTILINE)
        if count > 0:
            env_content = new_content
        else:
            env_content = env_content.rstrip() + f"\n{replacement}\n"

    env_content = re.sub(
        r"^COMPOSE_PROJECT_NAME=",
        "# COMPOSE_PROJECT_NAME=",
        env_content,
        flags=re.MULTILINE,
    )

    with open(env_path, "w") as f:
        f.write(env_content)
    os.chmod(env_path, 0o600)


def _write_policy(repo_path: str, gateway_url: str) -> None:
    """Write OnboardingPolicy config to the repo's config directory."""
    config_dir = f"{repo_path}/config"
    os.makedirs(config_dir, exist_ok=True)

    # Write YAML directly to avoid dependency on pyyaml in the CLI venv.
    # The structure is simple and static — no need for a YAML library.
    yaml_content = textwrap.dedent(f"""\
        policy:
          class: "{ONBOARDING_POLICY_CLASS}"
          config:
            gateway_url: "{gateway_url}"
    """)

    with open(f"{config_dir}/policy_config.yaml", "w") as f:
        f.write(yaml_content)


def _show_results(
    console: Console,
    gateway_url: str,
    mode: str,
) -> None:
    """Show the final success panel and prompt user to launch Claude Code."""
    config_url = f"{gateway_url}/policy-config"

    console.print()
    console.print(
        Panel(
            textwrap.dedent(f"""\
                [green bold]Gateway is running![/green bold]

                [bold]What was installed:[/bold]
                  [bold]luthien[/bold] CLI      — manage, configure, and interact with the proxy
                  [bold]luthien-proxy[/bold]    — the gateway server itself

                [bold]Gateway URL:[/bold]  {gateway_url}
                [bold]Policy:[/bold]       OnboardingPolicy (welcome on first turn)
                [bold]Mode:[/bold]         {mode}

                [bold]Configuration:[/bold]
                  CLI config:    [cyan]~/.luthien/config.toml[/cyan]
                  Gateway .env:  [cyan]~/.luthien/luthien-proxy/.env[/cyan]
                  View config:   [bold]luthien config[/bold]

                [bold]Configure policies:[/bold]  {config_url}

                [bold]Manage the gateway:[/bold]
                  luthien status     # check health
                  luthien logs       # view logs
                  luthien down       # stop the gateway
                  luthien up         # start again
                  [bold yellow]luthien claude[/bold yellow]    # launch Claude Code through the proxy

                [bold]Uninstall:[/bold]
                  uv tool uninstall luthien-cli

                [dim]Luthien sends anonymous usage data to help development.
                To disable, set USAGE_TELEMETRY=false in .env and restart.[/dim]"""),
            title="Ready",
            border_style="green",
        )
    )

    # Open the config UI in the browser
    try:
        webbrowser.open(config_url)
        console.print(f"[dim]Opened {config_url} in browser[/dim]")
    except Exception:
        console.print(f"[dim]Open {config_url} in your browser to configure policies[/dim]")

    # Prompt user to launch Claude Code (single keypress, no Enter needed)
    console.print()
    console.print("[bold]Press any key to launch Claude Code through Luthien Proxy, or q to quit.[/bold]")
    try:
        key = _read_single_key()
        if key.lower() == "q":
            return
    except (KeyboardInterrupt, EOFError):
        return

    # Launch Claude Code through the proxy with the onboarding prompt.
    # Use _exec_claude directly — the gateway is already confirmed
    # healthy above, and the keypress provides a human-scale delay
    # that lets all prior Rich ANSI terminal responses arrive and be
    # drained before exec.  Calling _launch_claude would re-run
    # ensure_gateway_up (Rich output) right before exec, re-opening
    # the ANSI race window.
    from luthien_cli.commands.claude import _exec_claude

    _exec_claude(gateway_url, [ONBOARDING_PROMPT])


def _onboard_local(
    console: Console,
    config,
    admin_key: str,
    proxy_ref: str | None = None,
    sentry_enabled: bool = False,
    sentry_dsn: str = "",
) -> None:
    """Onboard in local mode: SQLite, passthrough auth, no Docker."""
    # 1. Install gateway package
    console.print("[blue]Installing luthien-proxy...[/blue]")
    config.repo_path = ensure_gateway_venv(proxy_ref=proxy_ref, force_reinstall=True)
    console.print("[green]luthien CLI and proxy installed.[/green]")

    # 2. Stop any existing gateway BEFORE selecting a port, so the old
    #    gateway's port is freed and find_free_port can reclaim it.
    stop_gateway(config.repo_path)

    # 3. Find a free port (needed before writing policy config)
    gateway_port = find_free_port(8000)
    actual_gateway_url = f"http://localhost:{gateway_port}"

    # 4. Write config files
    console.print("\n[blue]Configuring gateway...[/blue]")
    _write_policy(config.repo_path, actual_gateway_url)
    _write_local_env(config.repo_path, admin_key, sentry_enabled, sentry_dsn)

    # 5. Start the gateway
    console.print(f"\n[blue]Starting gateway on port {gateway_port}...[/blue]")
    pid = start_gateway(config.repo_path, port=gateway_port, console=console)
    console.print(f"[dim]Gateway started (PID {pid})[/dim]")

    # 6. Save config
    config.gateway_url = actual_gateway_url
    config.api_key = None
    config.admin_key = admin_key
    config.mode = "local"
    save_config(config, DEFAULT_CONFIG_PATH)
    console.print("[green]CLI config saved to ~/.luthien/config.toml[/green]")

    # 7. Wait for health
    if not wait_for_healthy(actual_gateway_url, console=console):
        console.print("[red]Gateway did not become healthy within 60s[/red]")
        console.print("[dim]Check logs: luthien logs[/dim]")
        raise SystemExit(1)

    _show_results(console, actual_gateway_url.rstrip("/"), "local")


def _onboard_docker(
    console: Console,
    config,
    admin_key: str,
    sentry_enabled: bool = False,
    sentry_dsn: str = "",
    *,
    yes: bool = False,
) -> None:
    """Onboard in Docker mode: PostgreSQL + Redis via docker compose."""
    # 1. Ensure proxy files
    if not config.repo_path:
        config.repo_path = ensure_repo(force_update=True)

    # 2. Write env config
    console.print("\n[blue]Configuring gateway...[/blue]")
    _ensure_docker_env(config.repo_path, admin_key, sentry_enabled, sentry_dsn)

    # 3. Start Docker stack
    console.print("\n[blue]Starting gateway...[/blue]")
    use_local_build = False
    with console.status("Pulling latest images..."):
        pull_result = subprocess.run(
            ["docker", "compose", "pull"],
            cwd=config.repo_path,
            capture_output=True,
            text=True,
        )
    if pull_result.returncode != 0:
        stderr_lower = (pull_result.stderr or "").lower()
        if any(hint in stderr_lower for hint in _GHCR_AUTH_HINTS):
            console.print("[yellow]Docker image pull failed: access denied.[/yellow]")
        else:
            console.print("[yellow]Could not pull pre-built images from GHCR.[/yellow]")
        console.print(f"[dim]{(pull_result.stderr or '').strip()}[/dim]")
        console.print()

        if yes or click.confirm(
            "Would you like to build the Docker images locally instead? (requires git and takes a few minutes)",
            default=True,
        ):
            clone_path = ensure_repo_clone()
            # Build images from the clone, but keep config.repo_path on the
            # managed artifact directory.  The clone has the full source tree
            # needed for `docker compose build`, but the managed dir has the
            # stripped docker-compose.yaml (no bind mounts / build: blocks)
            # that subsequent `luthien up/down` should use.
            _ensure_docker_env(clone_path, admin_key, sentry_enabled, sentry_dsn)
            use_local_build = True

            with console.status("Building Docker images locally (this may take a few minutes)..."):
                build_result = subprocess.run(
                    ["docker", "compose", "build"],
                    cwd=clone_path,
                    capture_output=True,
                    text=True,
                )
            if build_result.returncode != 0:
                output = (build_result.stdout or "") + (build_result.stderr or "")
                console.print(f"[red]docker compose build failed:[/red]\n{output.strip()}")
                console.print(_LOCAL_MODE_HINT)
                raise SystemExit(1)
            console.print("[green]Docker images built successfully.[/green]")
        else:
            console.print(_LOCAL_MODE_HINT)
            raise SystemExit(1)

    with console.status("Stopping existing containers..."):
        down_result = subprocess.run(
            ["docker", "compose", "down", "--remove-orphans"],
            cwd=config.repo_path,
            capture_output=True,
            text=True,
        )
    if down_result.returncode == 0 and "Removed" in (down_result.stderr or ""):
        console.print("[dim]Stopped existing luthien containers.[/dim]")

    port_env = find_docker_ports()
    if port_env:
        selected = ", ".join(f"{k}={v}" for k, v in port_env.items())
        console.print(f"[dim]Auto-selected ports: {selected}[/dim]")

    gateway_port = port_env.get("GATEWAY_PORT", os.environ.get("GATEWAY_PORT", "8000"))
    actual_gateway_url = f"http://localhost:{gateway_port}"

    # Write policy config before starting containers so the gateway
    # reads the correct config at startup.
    _write_policy(config.repo_path, actual_gateway_url)

    with console.status("Starting containers..."):
        result = subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=config.repo_path,
            capture_output=True,
            text=True,
            env={**os.environ, **port_env},
        )
    if result.returncode != 0:
        console.print(f"[red]docker compose up failed:[/red]\n{result.stderr}")
        raise SystemExit(1)

    config.gateway_url = actual_gateway_url
    config.api_key = None
    config.admin_key = admin_key
    config.mode = "docker"
    save_config(config, DEFAULT_CONFIG_PATH)
    console.print("[green]CLI config saved to ~/.luthien/config.toml[/green]")

    if not wait_for_healthy(actual_gateway_url, console=console):
        console.print("[red]Gateway did not become healthy within 60s[/red]")
        console.print(f"[dim]Check logs: docker compose -f {config.repo_path}/docker-compose.yaml logs gateway[/dim]")
        raise SystemExit(1)

    mode_label = "docker (local build)" if use_local_build else "docker"
    _show_results(console, actual_gateway_url.rstrip("/"), mode_label)


@click.command()
@click.option("--docker", "use_docker", is_flag=True, help="Use Docker (PostgreSQL + Redis) instead of local mode")
@click.option("--proxy-ref", default=None, help="Git ref (branch, commit, or #PR) of luthien-proxy to install")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt")
def onboard(use_docker: bool, proxy_ref: str | None, yes: bool):
    """Set up a local Luthien gateway and start it with the onboarding policy."""
    console = Console()

    if use_docker and proxy_ref:
        console.print("[red]--proxy-ref is not supported with --docker mode.[/red]")
        raise SystemExit(1)

    if proxy_ref:
        proxy_ref = resolve_proxy_ref(proxy_ref)

    config = load_config(DEFAULT_CONFIG_PATH)

    mode_label = "Docker (PostgreSQL + Redis)" if use_docker else "local (SQLite, no Docker required)"
    console.print(
        Panel(
            "This will:\n"
            "  1. Install the [bold]luthien[/bold] CLI tool (via uv)\n"
            "  2. Install a local [bold]luthien-proxy[/bold] server\n"
            "  3. Start the proxy with an onboarding policy\n\n"
            f"[bold]Mode:[/bold] {mode_label}",
            title="Luthien Onboard",
            border_style="blue",
        )
    )

    if not yes:
        try:
            answer = console.input("[bold]Continue? [Y/n]: [/bold]")
            if answer.strip().lower() in ("n", "no"):
                console.print("[dim]Cancelled.[/dim]")
                return
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled.[/dim]")
            return

    admin_key = _generate_key("admin")

    # Sentry is off by default.  Users can enable it post-install by setting
    # SENTRY_ENABLED=true and SENTRY_DSN=<dsn> in ~/.luthien/luthien-proxy/.env.
    sentry_enabled = False
    sentry_dsn = ""

    if use_docker:
        _onboard_docker(console, config, admin_key, sentry_enabled, sentry_dsn, yes=yes)
    else:
        _onboard_local(
            console,
            config,
            admin_key,
            proxy_ref=proxy_ref,
            sentry_enabled=sentry_enabled,
            sentry_dsn=sentry_dsn,
        )
