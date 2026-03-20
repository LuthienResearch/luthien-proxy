"""luthien onboard -- set up and start a local gateway with the onboarding policy."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import textwrap
import webbrowser
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from luthien_cli.commands.up import wait_for_healthy
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config
from luthien_cli.local_process import find_docker_ports, find_free_port, start_gateway, stop_gateway
from luthien_cli.repo import ensure_gateway_venv, ensure_repo

ONBOARDING_POLICY_TEMPLATE = """\
policy:
  class: "luthien_proxy.policies.onboarding_policy:OnboardingPolicy"
  config:
    gateway_url: "{gateway_url}"
"""


def _generate_key(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


def _write_local_env(repo_path: str, proxy_key: str, admin_key: str) -> None:
    """Write .env for local mode (SQLite, no Redis)."""
    db_path = str(Path(repo_path) / "luthien.db")
    policy_path = str(Path(repo_path) / "config" / "policy_config.yaml")

    env_content = (
        f"DATABASE_URL=sqlite:///{db_path}\n"
        f"PROXY_API_KEY={proxy_key}\n"
        f"ADMIN_API_KEY={admin_key}\n"
        f"POLICY_SOURCE=file\n"
        f"POLICY_CONFIG={policy_path}\n"
        f"AUTH_MODE=both\n"
        f"OTEL_ENABLED=false\n"
        f"USAGE_TELEMETRY=true\n"
    )

    env_path = f"{repo_path}/.env"
    with open(env_path, "w") as f:
        f.write(env_content)
    os.chmod(env_path, 0o600)


def _ensure_docker_env(repo_path: str, proxy_key: str, admin_key: str) -> None:
    """Create or update .env with Docker onboard settings."""
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

    overrides = {
        "PROXY_API_KEY": proxy_key,
        "ADMIN_API_KEY": admin_key,
        "AUTH_MODE": "both",
        "POLICY_SOURCE": "file",
    }

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

    policy_yaml = ONBOARDING_POLICY_TEMPLATE.format(gateway_url=gateway_url)

    with open(f"{config_dir}/policy_config.yaml", "w") as f:
        f.write(policy_yaml)


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

                [bold]Gateway URL:[/bold]  {gateway_url}
                [bold]Policy:[/bold]       OnboardingPolicy (welcome on first turn)
                [bold]Mode:[/bold]         {mode}

                [bold]Configure policies:[/bold]  {config_url}

                [bold]Manage the gateway:[/bold]
                  luthien status     # check health
                  luthien logs       # view logs
                  luthien down       # stop the gateway
                  luthien up         # start again
                  [bold yellow]luthien claude[/bold yellow]    # launch Claude Code through the proxy

                [bold]Uninstall:[/bold]
                  pipx uninstall luthien-cli

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

    # Prompt user to launch Claude Code
    console.print()
    try:
        key = console.input("[bold]Press Enter to launch Claude Code through Luthien Proxy, or q to quit: [/bold]")
        if key.strip().lower() == "q":
            return
    except (KeyboardInterrupt, EOFError):
        return

    # Launch Claude Code through the proxy
    from luthien_cli.commands.claude import _launch_claude

    _launch_claude(console)


def _onboard_local(console: Console, config, proxy_key: str, admin_key: str) -> None:
    """Onboard in local mode: SQLite + in-process event publisher, no Docker."""
    # 1. Install gateway package
    console.print("[blue]Installing luthien-proxy...[/blue]")
    config.repo_path = ensure_gateway_venv()
    console.print("[green]luthien CLI and proxy installed.[/green]")

    # 2. Find a free port (needed before writing policy config)
    gateway_port = find_free_port(8000)
    actual_gateway_url = f"http://localhost:{gateway_port}"

    # 3. Write config files
    console.print("\n[blue]Configuring gateway...[/blue]")
    _write_policy(config.repo_path, actual_gateway_url)
    _write_local_env(config.repo_path, proxy_key, admin_key)

    # 4. Stop any existing gateway
    stop_gateway(config.repo_path)

    # 5. Start the gateway
    console.print(f"\n[blue]Starting gateway on port {gateway_port}...[/blue]")
    pid = start_gateway(config.repo_path, port=gateway_port, console=console)
    console.print(f"[dim]Gateway started (PID {pid})[/dim]")

    # 6. Save config
    config.gateway_url = actual_gateway_url
    config.api_key = proxy_key
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


def _onboard_docker(console: Console, config, proxy_key: str, admin_key: str) -> None:
    """Onboard in Docker mode: PostgreSQL + Redis via docker compose."""
    # 1. Ensure proxy files
    if not config.repo_path:
        config.repo_path = ensure_repo()

    # 2. Write env config
    console.print("\n[blue]Configuring gateway...[/blue]")
    _ensure_docker_env(config.repo_path, proxy_key, admin_key)

    # 3. Start Docker stack
    console.print("\n[blue]Starting gateway...[/blue]")
    with console.status("Pulling latest images..."):
        pull_result = subprocess.run(
            ["docker", "compose", "pull"],
            cwd=config.repo_path,
            capture_output=True,
            text=True,
        )
    if pull_result.returncode != 0:
        console.print(f"[red]docker compose pull failed:[/red]\n{pull_result.stderr}")
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

    gateway_port = port_env.get("GATEWAY_PORT", os.environ.get("GATEWAY_PORT", "8000"))
    actual_gateway_url = f"http://localhost:{gateway_port}"

    # Write policy config now that we know the gateway URL
    _write_policy(config.repo_path, actual_gateway_url)

    config.gateway_url = actual_gateway_url
    config.api_key = proxy_key
    config.admin_key = admin_key
    config.mode = "docker"
    save_config(config, DEFAULT_CONFIG_PATH)
    console.print("[green]CLI config saved to ~/.luthien/config.toml[/green]")

    if not wait_for_healthy(actual_gateway_url, console=console):
        console.print("[red]Gateway did not become healthy within 60s[/red]")
        console.print(f"[dim]Check logs: docker compose -f {config.repo_path}/docker-compose.yaml logs gateway[/dim]")
        raise SystemExit(1)

    _show_results(console, actual_gateway_url.rstrip("/"), "docker")


@click.command()
@click.option("--docker", "use_docker", is_flag=True, help="Use Docker (PostgreSQL + Redis) instead of local mode")
def onboard(use_docker: bool):
    """Set up a local Luthien gateway and start it with the onboarding policy."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    mode_label = "Docker (PostgreSQL + Redis)" if use_docker else "local (SQLite, no Docker required)"
    console.print(
        Panel(
            f"Installing the [bold]luthien[/bold] CLI tool and setting up a local gateway.\n\n"
            f"[bold]Mode:[/bold] {mode_label}",
            title="Luthien Onboard",
            border_style="blue",
        )
    )

    proxy_key = _generate_key("sk-luthien")
    admin_key = _generate_key("admin")

    if use_docker:
        _onboard_docker(console, config, proxy_key, admin_key)
    else:
        _onboard_local(console, config, proxy_key, admin_key)
