"""luthien hackathon -- one-command setup for hackathon participants."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

from luthien_cli.commands.up import wait_for_healthy
from luthien_cli.config import DEFAULT_CONFIG_PATH, load_config, save_config
from luthien_cli.local_process import find_free_port, stop_gateway

DEFAULT_CLONE_PATH = Path.home() / "luthien-proxy"
GITHUB_REPO = "LuthienResearch/luthien-proxy"
GITHUB_HTTPS_URL = f"https://github.com/{GITHUB_REPO}.git"

HACKATHON_PROMPT = (
    "I just joined the AI Control Hackathon and set up Luthien proxy! "
    "It's intercepting API traffic between Claude Code and the Anthropic backend. "
    "Please give a short response - the proxy's hackathon onboarding policy will "
    "append information about the hackathon, project ideas, and how to get started."
)

POLICY_CHOICES = {
    "1": (
        "HackathonOnboardingPolicy",
        "luthien_proxy.policies.hackathon_onboarding_policy:HackathonOnboardingPolicy",
        "Welcome message with hackathon context on first turn",
    ),
    "2": (
        "BlockDangerousCommandsPolicy",
        "luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy",
        "Blocks rm -rf, chmod 777, etc.",
    ),
    "3": (
        "NoYappingPolicy",
        "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy",
        "Removes filler, hedging, and preamble",
    ),
    "4": (
        "AllCapsPolicy",
        "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
        "Converts all response text to UPPERCASE",
    ),
    "5": (
        "NoOpPolicy",
        "luthien_proxy.policies.noop_policy:NoOpPolicy",
        "Clean passthrough, no modifications",
    ),
}

PID_FILE = "gateway.pid"
LOG_FILE = "gateway.log"


def _generate_key(prefix: str) -> str:
    return f"{prefix}-{secrets.token_urlsafe(24)}"


def _clone_repo(console: Console, clone_path: Path) -> bool:
    """Fork+clone or plain clone the repo. Returns True if repo is ready."""
    if clone_path.exists():
        git_dir = clone_path / ".git"
        if git_dir.exists():
            console.print(f"[dim]Repo already exists at {clone_path}, pulling latest...[/dim]")
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=clone_path,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(
                    "[yellow]git pull failed (you may have local changes). Continuing with existing code.[/yellow]"
                )
            return True
        else:
            console.print(f"[red]Directory {clone_path} exists but is not a git repo.[/red]")
            console.print("[dim]Choose a different path with --path or remove the directory.[/dim]")
            return False

    # Try gh fork first (gives participants their own fork for PRs)
    gh_path = shutil.which("gh")
    if gh_path:
        console.print("[blue]Forking and cloning repository...[/blue]")
        result = subprocess.run(
            [
                "gh",
                "repo",
                "fork",
                GITHUB_REPO,
                "--clone",
                "--default-branch-only",
                "--",
                str(clone_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print("[green]Forked and cloned successfully.[/green]")
            return True
        console.print("[yellow]gh fork failed, falling back to git clone...[/yellow]")

    # Fallback: plain git clone
    console.print("[blue]Cloning repository...[/blue]")
    result = subprocess.run(
        ["git", "clone", GITHUB_HTTPS_URL, str(clone_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]git clone failed:[/red]\n{result.stderr}")
        return False
    console.print("[green]Cloned successfully.[/green]")
    return True


def _install_deps(console: Console, repo_path: Path) -> bool:
    """Run uv sync --dev in the cloned repo."""
    uv = shutil.which("uv")
    if not uv:
        console.print("[red]uv is required but not found.[/red]")
        console.print("[dim]Install from https://docs.astral.sh/uv/[/dim]")
        return False

    console.print("[blue]Installing dependencies...[/blue]")
    with console.status("Running uv sync --dev..."):
        result = subprocess.run(
            [uv, "sync", "--dev"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        console.print(f"[red]uv sync failed:[/red]\n{result.stderr}")
        return False
    console.print("[green]Dependencies installed.[/green]")
    return True


def _pick_policy(console: Console, yes: bool) -> tuple[str, str]:
    """Interactive policy picker. Returns (policy_class_ref, display_name)."""
    if yes:
        choice = POLICY_CHOICES["1"]
        return choice[1], choice[0]

    console.print("\n[bold]Choose a starter policy:[/bold]")
    for key, (name, _, desc) in POLICY_CHOICES.items():
        default_marker = " [green](default)[/green]" if key == "1" else ""
        console.print(f"  [{key}] [bold]{name}[/bold]{default_marker} — {desc}")

    answer = console.input("\n[bold]Pick [1-5, default=1]: [/bold]").strip()
    if not answer:
        answer = "1"
    if answer not in POLICY_CHOICES:
        console.print(f"[yellow]Invalid choice '{answer}', using default.[/yellow]")
        answer = "1"

    choice = POLICY_CHOICES[answer]
    return choice[1], choice[0]


def _read_existing_keys(env_path: Path) -> tuple[str | None, str | None]:
    """Read existing PROXY_API_KEY and ADMIN_API_KEY from .env if present."""
    if not env_path.exists():
        return None, None
    proxy_key = None
    admin_key = None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("PROXY_API_KEY="):
            proxy_key = _parse_env_value(line.partition("=")[2].strip())
        elif line.startswith("ADMIN_API_KEY="):
            admin_key = _parse_env_value(line.partition("=")[2].strip())
    return proxy_key, admin_key


def _write_env(repo_path: Path, proxy_key: str, admin_key: str, port: int) -> tuple[str, str]:
    """Write .env for hackathon mode (SQLite, from source).

    Preserves existing API keys on re-runs to avoid breaking active sessions.
    Returns (proxy_key, admin_key) actually written (may differ from args if reusing existing).
    """
    env_path = repo_path / ".env"
    existing_proxy, existing_admin = _read_existing_keys(env_path)
    if existing_proxy:
        proxy_key = existing_proxy
    if existing_admin:
        admin_key = existing_admin

    db_path = str(repo_path / "luthien.db")
    policy_path = str(repo_path / "config" / "policy_config.yaml")

    env_content = (
        f"DATABASE_URL=sqlite:///{db_path}\n"
        f"PROXY_API_KEY={proxy_key}\n"
        f"ADMIN_API_KEY={admin_key}\n"
        f"POLICY_SOURCE=file\n"
        f"POLICY_CONFIG={policy_path}\n"
        f"AUTH_MODE=both\n"
        f"OTEL_ENABLED=false\n"
        f"USAGE_TELEMETRY=true\n"
        f"GATEWAY_PORT={port}\n"
    )
    env_path.write_text(env_content)
    os.chmod(env_path, 0o600)
    return proxy_key, admin_key


def _write_policy_config(repo_path: Path, policy_class_ref: str, gateway_url: str) -> None:
    """Write policy_config.yaml pointing at the chosen policy."""
    config_dir = repo_path / "config"
    config_dir.mkdir(exist_ok=True)

    # Write YAML directly to avoid dependency on pyyaml in the CLI venv.
    needs_gateway_url = "hackathon_onboarding_policy" in policy_class_ref
    if needs_gateway_url:
        yaml_content = textwrap.dedent(f"""\
            policy:
              class: "{policy_class_ref}"
              config:
                gateway_url: "{gateway_url}"
        """)
    else:
        yaml_content = textwrap.dedent(f"""\
            policy:
              class: "{policy_class_ref}"
              config: {{}}
        """)

    with open(config_dir / "policy_config.yaml", "w") as f:
        f.write(yaml_content)


def _parse_env_value(value: str) -> str:
    """Strip surrounding quotes from a .env value.

    Only handles simple KEY=value lines. Does not support multi-line values,
    escaped quotes, export prefixes, or inline comments.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _start_hackathon_gateway(console: Console, repo_path: Path, port: int) -> int:
    """Start the gateway from source using uv run. Returns PID."""
    if sys.platform == "win32":
        raise RuntimeError("Local mode requires Unix (Linux/macOS).")

    # Stop existing gateway if running
    pid_path = repo_path / PID_FILE
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            os.kill(old_pid, 0)
            stop_gateway(str(repo_path), console=console)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv not found")

    log_path = repo_path / LOG_FILE

    env = os.environ.copy()
    env_file = repo_path / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = _parse_env_value(value.strip())
    env["GATEWAY_PORT"] = str(port)

    with open(log_path, "a") as log_handle:
        proc = subprocess.Popen(
            [uv, "run", "python", "-m", "luthien_proxy.main"],
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            env=env,
            cwd=str(repo_path),
        )

    try:
        pid_path.write_text(str(proc.pid))
    except Exception:
        proc.terminate()
        raise

    return proc.pid


def _show_hackathon_guide(
    console: Console,
    gateway_url: str,
    repo_path: Path,
    policy_name: str,
) -> None:
    """Print the hackathon getting-started guide."""

    console.print()
    console.print(
        Panel(
            textwrap.dedent(f"""\
                [bold]Scripts:[/bold]
                  ./scripts/start_gateway.sh          Start gateway (no Docker)
                  ./scripts/dev_checks.sh             Format + lint + typecheck + test
                  uv run pytest tests/unit_tests/ -x  Quick unit tests (stop on first failure)
                  uv run pytest tests/unit_tests/policies/test_hackathon_policy_template.py -v

                [bold]Hot-reload your policy (no restart needed):[/bold]
                  curl -X POST {gateway_url}/api/admin/policy/set \\
                    -H "Authorization: Bearer $ADMIN_API_KEY" \\
                    -H "Content-Type: application/json" \\
                    -d '{{"policy_class_ref": "luthien_proxy.policies.hackathon_policy_template:HackathonPolicy"}}'

                [dim]Your ADMIN_API_KEY is in .env in the repo root.[/dim]

                [bold]Or edit config/policy_config.yaml and restart the gateway.[/bold]"""),
            title="Cheatsheet",
            border_style="cyan",
        )
    )

    console.print(
        Panel(
            textwrap.dedent(f"""\
                {gateway_url}/policy-config        Visual policy picker and config editor
                {gateway_url}/activity/monitor     Live stream of requests and responses
                {gateway_url}/diffs                Before/after policy transformation diffs
                {gateway_url}/request-logs/viewer  Full HTTP request/response log viewer
                {gateway_url}/health               Gateway health check"""),
            title="UI Tour",
            border_style="magenta",
        )
    )

    console.print(
        Panel(
            textwrap.dedent("""\
                [bold]Start here:[/bold]
                  src/luthien_proxy/policies/hackathon_policy_template.py    YOUR policy
                  src/luthien_proxy/policies/all_caps_policy.py              Simplest example (27 lines)
                  src/luthien_proxy/policy_core/text_modifier_policy.py      Easiest base class
                  config/policy_config.yaml                                  Active policy config

                [bold]Go deeper:[/bold]
                  src/luthien_proxy/policies/simple_policy.py                Medium complexity base
                  src/luthien_proxy/policies/tool_call_judge_policy.py       Advanced: LLM judge
                  ARCHITECTURE.md                                            Full system design
                  docs/policies.md                                           Policy reference"""),
            title="Key Files",
            border_style="blue",
        )
    )

    console.print(
        Panel(
            textwrap.dedent("""\
                1. [bold]Resampling Policy[/bold] — if a judge rejects a response, resample instead of blocking
                2. [bold]Trusted Model Reroute[/bold] — route flagged tool calls to a trusted model
                3. [bold]Proxy Commands[/bold] — /luthien: prefixes in messages trigger proxy-side scripts
                4. [bold]Live Policy Editor[/bold] — ^^^describe changes^^^ inline while coding
                5. [bold]Character Injection[/bold] — pirate/anime/Shakespeare personas + code quality
                6. [bold]Model Router[/bold] — sonnet:/haiku: prefixes route to different models
                7. [bold]Self-Modifying Policy[/bold] — evolves based on conversation context
                8. [bold]Red Team[/bold] — try to extract hidden state through prompt injection

                More ideas: https://luthienresearch.github.io/luthien-pbc-site/hackathon/"""),
            title="Project Ideas",
            border_style="yellow",
        )
    )

    console.print(
        Panel(
            textwrap.dedent("""\
                Hackathon:   https://luthienresearch.github.io/luthien-pbc-site/hackathon/
                GitHub:      https://github.com/LuthienResearch/luthien-proxy
                Docs:        ARCHITECTURE.md in your cloned repo"""),
            title="Links",
            border_style="green",
        )
    )

    console.print()
    console.print(
        Panel(
            textwrap.dedent(f"""\
                [green bold]Gateway is running![/green bold]

                [bold]Gateway URL:[/bold]   {gateway_url}
                [bold]Policy:[/bold]        {policy_name}
                [bold]Repo:[/bold]          {repo_path}

                [bold]Manage the gateway:[/bold]
                  luthien status     # check health
                  luthien logs       # view logs
                  luthien down       # stop the gateway
                  luthien up         # start again
                  [bold yellow]luthien claude[/bold yellow]    # launch Claude Code through the proxy"""),
            title="Ready",
            border_style="green",
        )
    )


def _checkout_proxy_ref(console: Console, repo_path: Path, ref: str, pr_number: int | None = None) -> bool:
    """Checkout a specific ref in the cloned repo.

    For PRs, fetches the PR head first. For branches/SHAs, does a plain checkout.
    """
    if pr_number is not None:
        console.print(f"[blue]Fetching PR #{pr_number}...[/blue]")
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", f"pull/{pr_number}/head:{ref}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            console.print(f"[red]Failed to fetch PR #{pr_number}:[/red]\n{fetch_result.stderr}")
            return False

    result = subprocess.run(
        ["git", "checkout", ref],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]Failed to checkout {ref}:[/red]\n{result.stderr}")
        return False

    console.print(f"[green]Checked out {ref}[/green]")
    return True


@click.command()
@click.option("--path", default=str(DEFAULT_CLONE_PATH), help="Where to clone the repo")
@click.option("--proxy-ref", default=None, help="Git ref (branch, commit, or #PR) of luthien-proxy to use")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompts")
def hackathon(path: str, proxy_ref: str | None, yes: bool) -> None:
    """Set up for the AI Control Hackathon — fork, clone, install, and start hacking."""
    console = Console()
    clone_path = Path(path).expanduser().resolve()

    console.print(
        Panel(
            textwrap.dedent("""\
                Welcome to the [bold]AI Control Hackathon[/bold]!

                This will:
                  1. Fork & clone the luthien-proxy repo
                  2. Install dependencies
                  3. Start a local gateway
                  4. Create a starter policy template for you
                  5. Show you everything you need to start hacking

                [dim]No Docker required. Uses SQLite for storage.
                Sends anonymous usage telemetry to help development.
                To disable: set USAGE_TELEMETRY=false in .env[/dim]"""),
            title="Luthien Hackathon",
            border_style="yellow",
        )
    )

    if not yes:
        try:
            answer = console.input(f"[bold]Clone to {clone_path}? [Y/n]: [/bold]")
            if answer.strip().lower() in ("n", "no"):
                console.print("[dim]Cancelled.[/dim]")
                return
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled.[/dim]")
            return

    # 1. Clone/fork
    if not _clone_repo(console, clone_path):
        raise SystemExit(1)

    # 1.5 Checkout specific ref if requested
    if proxy_ref:
        pr_number = None
        if proxy_ref.startswith("#"):
            pr_number = int(proxy_ref[1:])
            ref_branch = f"pr-{pr_number}"
        else:
            ref_branch = proxy_ref
        if not _checkout_proxy_ref(console, clone_path, ref_branch, pr_number=pr_number):
            raise SystemExit(1)

    # 2. Install deps
    if not _install_deps(console, clone_path):
        raise SystemExit(1)

    # 3. Pick policy
    policy_class_ref, policy_name = _pick_policy(console, yes)

    # 4. Configure
    proxy_key = _generate_key("sk-luthien")
    admin_key = _generate_key("admin")
    gateway_port = find_free_port(8000)
    gateway_url = f"http://localhost:{gateway_port}"

    console.print("\n[blue]Configuring gateway...[/blue]")
    proxy_key, admin_key = _write_env(clone_path, proxy_key, admin_key, gateway_port)
    _write_policy_config(clone_path, policy_class_ref, gateway_url)

    # 5. Start gateway from source
    console.print(f"[blue]Starting gateway on port {gateway_port}...[/blue]")
    pid = _start_hackathon_gateway(console, clone_path, gateway_port)
    console.print(f"[dim]Gateway started (PID {pid})[/dim]")

    # 6. Save CLI config
    config = load_config(DEFAULT_CONFIG_PATH)
    config.gateway_url = gateway_url
    config.api_key = proxy_key
    config.admin_key = admin_key
    config.mode = "local"
    config.repo_path = str(clone_path)
    save_config(config, DEFAULT_CONFIG_PATH)
    console.print("[green]CLI config saved to ~/.luthien/config.toml[/green]")

    # 7. Wait for healthy
    if not wait_for_healthy(gateway_url, console=console):
        console.print("[red]Gateway did not become healthy within 60s[/red]")
        console.print("[dim]Check logs: luthien logs[/dim]")
        raise SystemExit(1)

    # 8. Show guide
    _show_hackathon_guide(console, gateway_url, clone_path, policy_name)

    # 9. Launch Claude Code
    console.print("[bold]Press any key to launch Claude Code through the proxy, or q to quit.[/bold]")
    try:
        key = click.getchar()
        if key.lower() == "q":
            return
    except (KeyboardInterrupt, EOFError):
        return

    from luthien_cli.commands.claude import _launch_claude

    _launch_claude(console, [HACKATHON_PROMPT])
