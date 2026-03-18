"""Managed proxy artifact directory at ~/.luthien/luthien-proxy/."""

from __future__ import annotations

import re
from pathlib import Path

import click
import httpx
from rich.console import Console

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/LuthienResearch/luthien-proxy/main/"
GITHUB_SHA_URL = "https://api.github.com/repos/LuthienResearch/luthien-proxy/commits/main"
MANAGED_REPO_DIR = Path.home() / ".luthien" / "luthien-proxy"

FILES_TO_DOWNLOAD = ("docker-compose.yaml", ".env.example")

# Matches the ./src volume mount line
_SRC_MOUNT_RE = re.compile(r"^ *- \./src:/app/src.*\n", re.MULTILINE)


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
    (dest / "config").mkdir(exist_ok=True)

    for filename in FILES_TO_DOWNLOAD:
        url = f"{GITHUB_RAW_BASE}{filename}"
        try:
            r = httpx.get(url, timeout=15.0, follow_redirects=True)
            r.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            console.print(
                f"[red]Could not download {url} from GitHub. Check your internet connection.[/red]\n[dim]{e}[/dim]"
            )
            raise SystemExit(1)
        except httpx.HTTPStatusError as e:
            console.print(f"[red]Failed to download {url}: HTTP {e.response.status_code}[/red]")
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


def ensure_repo() -> str:
    """Ensure managed proxy directory exists and is up to date. Returns path."""
    console = Console(stderr=True)
    dest = MANAGED_REPO_DIR

    has_version = (dest / ".version").is_file()
    has_compose = (dest / "docker-compose.yaml").is_file()

    if has_version and has_compose:
        local_sha = (dest / ".version").read_text().strip()
        try:
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
