"""Proxy version from package metadata.

hatch-vcs writes the version at build time (from git state). In Docker builds
where .git/ is excluded, SETUPTOOLS_SCM_PRETEND_VERSION provides the version
instead. Either way, `importlib.metadata.version()` is the single runtime path.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    PROXY_VERSION = version("luthien-proxy")
except PackageNotFoundError:
    PROXY_VERSION = "unknown"
