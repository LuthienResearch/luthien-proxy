"""luthien onboard -- interactive setup for a local gateway with a policy."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import textwrap

import click
from rich.console import Console
from rich.panel import Panel

from luthien_cli.commands.up import wait_for_healthy
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config
from luthien_cli.repo import ensure_repo

POLICY_TEMPLATE = """\
policy:
  class: "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"
  config:
    model: "claude-haiku-4-5"
    instructions: |
{instructions}
    temperature: 0.0
    max_tokens: 4096
    on_error: "pass"
"""


def _generate_key(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


def _indent_instructions(text: str, indent: int = 6) -> str:
    """Indent each line of the instructions for YAML block scalar."""
    lines = text.strip().splitlines()
    prefix = " " * indent
    return "\n".join(f"{prefix}{line}" for line in lines)


def _ensure_env(repo_path: str, proxy_key: str, admin_key: str) -> None:
    """Create or update .env with onboard settings."""
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

    with open(env_path, "w") as f:
        f.write(env_content)


def _write_policy(repo_path: str, instructions: str) -> None:
    """Write SimpleLLMPolicy config to the repo's config directory."""
    config_dir = f"{repo_path}/config"
    os.makedirs(config_dir, exist_ok=True)

    policy_yaml = POLICY_TEMPLATE.format(
        instructions=_indent_instructions(instructions),
    )

    with open(f"{config_dir}/policy_config.yaml", "w") as f:
        f.write(policy_yaml)


@click.command()
def onboard():
    """Interactive setup: configure a policy and start the gateway."""
    console = Console()
    config = load_config(DEFAULT_CONFIG_PATH)

    console.print(
        Panel(
            "This will set up a local Luthien gateway with a policy you describe,\n"
            "then show you how to route Claude Code through it.",
            title="Luthien Onboard",
            border_style="blue",
        )
    )

    # 1. Ensure proxy files are available
    config.repo_path = ensure_repo()

    # 2. Generate keys
    proxy_key = _generate_key("sk-luthien")
    admin_key = _generate_key("admin")

    # 3. Prompt for policy description
    console.print("\n[bold]Describe the policy you'd like applied to LLM responses.[/bold]")
    console.print(
        "[dim]Examples:\n"
        '  "Block any PII such as SSNs, credit card numbers, and email addresses"\n'
        '  "Ensure responses are professional and appropriate for a workplace"\n'
        '  "Redact any internal hostnames or IP addresses"[/dim]\n'
    )
    instructions = click.prompt("Policy instructions")

    # 4. Write config files
    console.print("\n[blue]Configuring gateway...[/blue]")
    _write_policy(config.repo_path, instructions)
    _ensure_env(config.repo_path, proxy_key, admin_key)

    # 5. Save CLI config
    config.api_key = proxy_key
    config.admin_key = admin_key
    save_config(config, DEFAULT_CONFIG_PATH)
    console.print("[green]CLI config saved to ~/.luthien/config.toml[/green]")

    # 6. Start the stack
    console.print("\n[blue]Starting gateway...[/blue]")
    # Pull latest images
    pull_result = subprocess.run(
        ["docker", "compose", "pull"],
        cwd=config.repo_path,
        capture_output=True,
        text=True,
    )
    if pull_result.returncode != 0:
        console.print(f"[red]docker compose pull failed:[/red]\n{pull_result.stderr}")
        raise SystemExit(1)
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=config.repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]docker compose up failed:[/red]\n{result.stderr}")
        raise SystemExit(1)

    console.print("[yellow]Waiting for gateway to be healthy...[/yellow]")
    if not wait_for_healthy(config.gateway_url):
        console.print("[red]Gateway did not become healthy within 60s[/red]")
        console.print(f"[dim]Check logs: docker compose -f {config.repo_path}/docker-compose.yaml logs gateway[/dim]")
        raise SystemExit(1)

    # 7. Show results
    gateway_url = config.gateway_url.rstrip("/")
    console.print()
    console.print(
        Panel(
            textwrap.dedent(f"""\
                [green bold]Gateway is running![/green bold]

                [bold]Gateway URL:[/bold]  {gateway_url}
                [bold]Policy:[/bold]       SimpleLLMPolicy
                [bold]Auth mode:[/bold]    both (proxy key or Anthropic key)

                [bold]Policy instructions:[/bold]
                [dim]{instructions}[/dim]

                [bold yellow]Launch Claude Code through the gateway:[/bold yellow]
                  luthien claude

                [bold]Or manually:[/bold]
                  ANTHROPIC_BASE_URL={gateway_url}/ ANTHROPIC_API_KEY={proxy_key} claude

                [dim]Luthien sends anonymous usage data to help development.
                To disable, set USAGE_TELEMETRY=false in .env and restart.[/dim]"""),
            title="Ready",
            border_style="green",
        )
    )
