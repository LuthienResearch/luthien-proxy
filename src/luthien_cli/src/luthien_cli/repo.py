"""Managed proxy artifact directory at ~/.luthien/luthien-proxy/."""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import click
import httpx
from rich.console import Console

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/LuthienResearch/luthien-proxy/main/"
GITHUB_SHA_URL = "https://api.github.com/repos/LuthienResearch/luthien-proxy/commits/main"
GITHUB_PR_URL = "https://api.github.com/repos/LuthienResearch/luthien-proxy/pulls/{number}"
MANAGED_REPO_DIR = Path.home() / ".luthien" / "luthien-proxy"
MANAGED_VENV_DIR = Path.home() / ".luthien" / "venv"
CLONE_DIR = Path.home() / ".luthien" / "luthien-proxy-src"
GITHUB_CLONE_URL = "https://github.com/LuthienResearch/luthien-proxy.git"

FILES_TO_DOWNLOAD = ("docker-compose.yaml", ".env.example")

# Matches the ./src volume mount line
_SRC_MOUNT_RE = re.compile(r"^ *- \./src:/app/src.*\n", re.MULTILINE)


def resolve_proxy_ref(ref: str) -> str:
    """Resolve a proxy ref string to a git ref.

    Plain strings (branches, tags, SHAs) pass through.
    '#N' resolves PR N's head branch via GitHub API.
    """
    if not ref.startswith("#"):
        return ref

    pr_number = ref[1:]
    console = Console(stderr=True)
    url = GITHUB_PR_URL.format(number=pr_number)
    try:
        r = httpx.get(url, timeout=10.0)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Could not find PR #{pr_number}: HTTP {e.response.status_code}[/red]")
        raise SystemExit(1)
    except httpx.HTTPError as e:
        console.print(f"[red]Could not resolve PR #{pr_number}: {e!r}[/red]")
        raise SystemExit(1)

    branch = r.json()["head"]["ref"]
    console.print(f"[blue]PR #{pr_number} → branch {branch}[/blue]")
    return branch


def _remove_build_blocks(content: str) -> str:
    """Remove build: blocks, keeping only lines at the same or lesser indent."""
    lines = content.splitlines(keepends=True)
    result: list[str] = []
    skip_indent = -1
    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if skip_indent >= 0:
            if indent > skip_indent:
                continue
            skip_indent = -1
        if stripped.startswith("build:"):
            skip_indent = indent
            continue
        result.append(line)
    return "".join(result)


def _get_remote_sha() -> str:
    """Get the latest commit SHA of the main branch."""
    r = httpx.get(
        GITHUB_SHA_URL,
        headers={"Accept": "application/vnd.github.sha"},
        timeout=10.0,
    )
    r.raise_for_status()
    return r.text.strip()


def _strip_dev_only_lines(content: str) -> str:
    """Remove dev-only lines from docker-compose.yaml.

    Strips the ./src volume mount (source is baked into the GHCR image)
    and build: blocks (no local Dockerfiles in managed install).
    """
    content = _remove_build_blocks(content)
    content = _SRC_MOUNT_RE.sub("", content)
    return content


def _download_files(dest: Path) -> None:
    """Download proxy artifacts from GitHub and write to dest."""
    console = Console(stderr=True)
    dest.mkdir(parents=True, exist_ok=True)
    config_dir = dest / "config"
    config_dir.mkdir(exist_ok=True)

    # Write default policy config if it doesn't exist
    policy_config = config_dir / "policy_config.yaml"
    if not policy_config.exists():
        policy_config.write_text(
            textwrap.dedent("""\
            # Luthien Policy Configuration
            # Default: pass-through (no modifications)
            policy:
              class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
              config: {}
            """)
        )

    with console.status("Downloading proxy files from GitHub..."):
        for filename in FILES_TO_DOWNLOAD:
            url = f"{GITHUB_RAW_BASE}{filename}"
            try:
                r = httpx.get(url, timeout=15.0, follow_redirects=True)
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    console.print(
                        f"[red]Failed to download {url}: HTTP {e.response.status_code} (access denied).[/red]\n"
                        "The resource may not be publicly accessible.\n"
                        "If this is a private repository, check your GitHub credentials."
                    )
                else:
                    console.print(f"[red]Failed to download {url}: HTTP {e.response.status_code}[/red]")
                raise SystemExit(1)
            except httpx.HTTPError as e:
                console.print(
                    f"[red]Could not download {url} from GitHub. Check your internet connection.[/red]\n[dim]{e}[/dim]"
                )
                raise SystemExit(1)

            content = r.text
            if filename == "docker-compose.yaml":
                content = _strip_dev_only_lines(content)

            (dest / filename).write_text(content)

        try:
            sha = _get_remote_sha()
        except (httpx.HTTPError, httpx.TimeoutException):
            sha = "unknown"
        (dest / ".version").write_text(sha)


def ensure_repo(*, force_update: bool = False) -> str:
    """Ensure managed proxy directory exists and is up to date. Returns path.

    Args:
        force_update: Always re-download files, even if local SHA matches remote.
    """
    console = Console(stderr=True)
    dest = MANAGED_REPO_DIR

    has_version = (dest / ".version").is_file()
    has_compose = (dest / "docker-compose.yaml").is_file()

    if has_version and has_compose and not force_update:
        local_sha = (dest / ".version").read_text().strip()
        try:
            with console.status("Checking for updates..."):
                remote_sha = _get_remote_sha()
        except (httpx.HTTPError, httpx.TimeoutException):
            console.print("[yellow]Could not check for updates (network error). Using existing files.[/yellow]")
            return str(dest)

        if remote_sha == local_sha:
            return str(dest)

        if click.confirm("A newer version of luthien-proxy is available. Update?", default=True):
            console.print("[blue]Updating proxy files...[/blue]")
            _download_files(dest)
        return str(dest)

    console.print("[blue]Downloading luthien-proxy files...[/blue]")
    _download_files(dest)
    return str(dest)


def _run_uv(*args: str, console: Console) -> None:
    """Run a uv command, raising SystemExit on failure."""
    uv = shutil.which("uv")
    if not uv:
        console.print("[red]uv is required but not found. Install from https://docs.astral.sh/uv/[/red]")
        raise SystemExit(1)

    result = subprocess.run(
        [uv, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]uv {' '.join(args)} failed:[/red]\n{result.stderr}")
        raise SystemExit(1)


def ensure_gateway_venv(
    proxy_ref: str | None = None,
    *,
    force_reinstall: bool = False,
) -> str:
    """Create a managed venv and install luthien-proxy. Returns repo path.

    Creates ~/.luthien/venv/ with luthien-proxy installed, and ensures
    the ~/.luthien/luthien-proxy/ directory exists for config/data files.

    Args:
        proxy_ref: Git ref (branch, tag, SHA) to install from.
        force_reinstall: Always re-fetch from GitHub, even if already installed.
    """
    console = Console(stderr=True)
    venv_dir = MANAGED_VENV_DIR
    repo_dir = MANAGED_REPO_DIR

    repo_dir.mkdir(parents=True, exist_ok=True)
    config_dir = repo_dir / "config"
    config_dir.mkdir(exist_ok=True)

    # Write default policy config if it doesn't exist
    policy_config = config_dir / "policy_config.yaml"
    if not policy_config.exists():
        policy_config.write_text(
            textwrap.dedent("""\
            # Luthien Policy Configuration
            # Default: pass-through (no modifications)
            policy:
              class: "luthien_proxy.policies.noop_policy:NoOpPolicy"
              config: {}
            """)
        )

    venv_python = venv_dir / "bin" / "python"
    needs_install = not venv_python.exists()

    if needs_install:
        console.print("[blue]Creating gateway environment...[/blue]")
        with console.status("Setting up Python environment..."):
            _run_uv("venv", str(venv_dir), "--python", "3.13", console=console)

    github_source = "git+https://github.com/LuthienResearch/luthien-proxy.git"
    if proxy_ref is not None:
        console.print(f"[blue]Using proxy ref: {proxy_ref}[/blue]")
        github_source = f"{github_source}@{proxy_ref}"

    install_args = [
        "pip",
        "install",
        "--python",
        str(venv_python),
    ]

    # Force a fresh fetch when explicitly requested, when a specific ref
    # is given, or on first install. Without this, uv sees "already installed"
    # for git sources and skips the fetch even if upstream has new commits.
    if force_reinstall or proxy_ref is not None or needs_install:
        install_args += ["--reinstall-package", "luthien-proxy"]
        label = "Installing latest luthien-proxy..."
    else:
        install_args.append("--upgrade")
        label = "Checking luthien-proxy..."

    install_args.append(github_source)

    with console.status(label):
        _run_uv(*install_args, console=console)

    console.print("[green]Gateway package installed.[/green]")
    return str(repo_dir)


def ensure_repo_clone() -> str:
    """Clone or update the full luthien-proxy repo for local Docker builds.

    Returns the path to the cloned repo at ~/.luthien/luthien-proxy-src/.
    If the clone already exists, pulls latest changes instead.
    """
    console = Console(stderr=True)
    dest = CLONE_DIR

    git = shutil.which("git")
    if not git:
        console.print("[red]git is required for local builds but not found.[/red]")
        raise SystemExit(1)

    if (dest / ".git").is_dir():
        # Use fetch+reset instead of pull --ff-only: shallow clones can
        # fail to fast-forward when the remote has diverged.
        with console.status("Updating local repo clone..."):
            fetch = subprocess.run(
                [git, "fetch", "--depth", "1", "origin", "main"],
                cwd=str(dest),
                capture_output=True,
                text=True,
            )
            if fetch.returncode == 0:
                result = subprocess.run(
                    [git, "reset", "--hard", "origin/main"],
                    cwd=str(dest),
                    capture_output=True,
                    text=True,
                )
            else:
                result = fetch
        if result.returncode != 0:
            console.print("[yellow]Could not update repo clone. Using existing files.[/yellow]")
    else:
        with console.status("Cloning luthien-proxy repo for local build..."):
            result = subprocess.run(
                [git, "clone", "--depth", "1", GITHUB_CLONE_URL, str(dest)],
                capture_output=True,
                text=True,
            )
        if result.returncode != 0:
            console.print(f"[red]Failed to clone repo:[/red]\n{result.stderr}")
            raise SystemExit(1)

    console.print("[green]Repo clone ready for local build.[/green]")
    return str(dest)
