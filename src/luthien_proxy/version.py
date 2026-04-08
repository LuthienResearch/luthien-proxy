"""Proxy version from package metadata.

hatch-vcs writes the version at build time (from git state). In Docker builds
where .git/ is excluded, SETUPTOOLS_SCM_PRETEND_VERSION provides the version
instead. Either way, `importlib.metadata.version()` is the single runtime path.

PROXY_VERSION is the full PEP 440 string (e.g. '0.1.20.dev2+g64a517c2').
PROXY_DISPLAY_VERSION is a short form for UIs (e.g. '64a517c2').
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version

try:
    PROXY_VERSION = version("luthien-proxy")
except PackageNotFoundError:
    PROXY_VERSION = "unknown"


def _short_version(full: str) -> str:
    """Extract a human-friendly short version from the PEP 440 string.

    hatch-vcs produces versions like '0.1.20.dev2+g64a517c2.d20260407'.
    Docker builds produce '0.0.0+<commit>'. Tagged releases produce '1.0.0'.
    """
    # Tagged release — no local part, use as-is
    if "+" not in full:
        return full
    local = full.split("+", 1)[1]
    # Strip the 'g' prefix that hatch-vcs adds to git hashes, and any .dYYYYMMDD suffix
    local = re.sub(r"\.d\d{8}$", "", local)
    if local.startswith("g"):
        local = local[1:]
    return local


PROXY_DISPLAY_VERSION = _short_version(PROXY_VERSION)
