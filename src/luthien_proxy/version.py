"""Proxy version derived from git state.

The proxy doesn't use semver tags, so the "version" is the short commit hash
of the running code. Computed once at import time.

Resolution order:
1. LUTHIEN_BUILD_COMMIT env var (set by CLI when launching the gateway)
2. A BUILD_COMMIT file baked into the package directory (Docker builds)
3. Live `git rev-parse --short HEAD` (dev checkouts)
4. "unknown"
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_PACKAGE_DIR = Path(__file__).parent


def _read_build_commit() -> str | None:
    """Read commit hash baked in at Docker build time."""
    build_file = _PACKAGE_DIR / "BUILD_COMMIT"
    if build_file.is_file():
        content = build_file.read_text().strip()
        if content and content != "unknown":
            return content[:8]
    return None


def _get_git_commit() -> str | None:
    """Return the short git commit hash, or None if git isn't available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(_PACKAGE_DIR),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _resolve_version() -> str:
    return os.environ.get("LUTHIEN_BUILD_COMMIT") or _read_build_commit() or _get_git_commit() or "unknown"


PROXY_VERSION = _resolve_version()
