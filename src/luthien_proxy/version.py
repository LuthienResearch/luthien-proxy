"""Proxy version from package metadata.

hatch-vcs writes the version at build time (from git state). In Docker builds
where .git/ is excluded, the Dockerfile pre-writes _version.py from a build arg
before `uv sync`. Either way, `importlib.metadata.version()` is the single
runtime path.
"""

from __future__ import annotations

from importlib.metadata import version

PROXY_VERSION = version("luthien-proxy")
