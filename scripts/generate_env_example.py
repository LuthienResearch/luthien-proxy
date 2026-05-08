#!/usr/bin/env python3
"""Generate .env.example from config field definitions.

Usage:
    uv run python scripts/generate_env_example.py > .env.example
"""

import sys
from enum import Enum
from pathlib import Path

# Add src to path so we can import config_fields when run as a script
# (the insert is harmless when imported from tests because the installed
# package is already on sys.path — we just end up with an extra entry).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from luthien_proxy.config_fields import CONFIG_CATEGORIES, CONFIG_FIELDS

# Non-registry env vars: features that read os.environ directly because their
# value shape (e.g. JSON blob) doesn't fit the scalar config registry. Listed
# here so they remain discoverable in .env.example without the registry's type
# system and dashboard plumbing.
EXTRA_ENV_VARS: tuple[tuple[str, str, str], ...] = (
    (
        "UPSTREAM_HEADERS",
        "JSON object mapping header name → template string. Templates support "
        "${session_id}, ${request_path}, and ${env.VARNAME}. Headers expand "
        "per-request and are merged into outbound LLM requests. Misconfiguration "
        "fails the gateway at startup. See pipeline/upstream_headers.py.",
        '{"Helicone-Auth":"Bearer ${env.HELICONE_API_KEY}","Helicone-Session-Id":"${session_id}"}',
    ),
)


def _format_default(value: object) -> str:
    """Render a default value as it should appear after ``VAR=`` in .env.

    Enums need their ``.value``, not their repr — otherwise ``AUTH_MODE`` would
    render as ``AuthMode.BOTH`` and a user uncommenting the line would set
    the literal string ``AuthMode.BOTH`` as the env var, which is not a valid
    ``AuthMode`` member and would fail at startup.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def build_env_example_text() -> str:
    """Return the full generated .env.example text.

    Exposed separately from `main()` so tests and other tooling can compare
    against the canonical output without spawning a subprocess.
    """
    lines = [
        "# Luthien Proxy — Environment Configuration",
        "# Auto-generated from config field definitions (scripts/generate_env_example.py).",
        "# Copy to .env and edit as needed.",
        "",
    ]
    current_category: str | None = None

    fields_by_cat: dict[str, list] = {}
    for meta in CONFIG_FIELDS:
        fields_by_cat.setdefault(meta.category, []).append(meta)

    for cat in CONFIG_CATEGORIES:
        cat_fields = fields_by_cat.get(cat, [])
        if not cat_fields:
            continue
        if current_category is not None:
            lines.append("")
        current_category = cat

        bar = "=" * (60 - len(cat))
        lines.append(f"# === {cat.upper()} {bar}")
        lines.append("")

        for meta in cat_fields:
            lines.append(f"# {meta.description}")
            if meta.sensitive:
                lines.append("# (sensitive)")
            if meta.db_settable:
                lines.append("# (can also be set at runtime via admin API)")

            if meta.dynamic_default:
                # Dynamic defaults (e.g. PROXY_VERSION from package metadata)
                # must not be baked into .env.example — their resolved value
                # depends on the build environment and would create spurious
                # drift between local and CI generation.
                symbol = meta.default_from[1] if meta.default_from else "runtime"
                lines.append(f"# (default derived from {symbol} at startup)")
                lines.append(f"# {meta.env_var}=")
            else:
                lines.append(f"# {meta.env_var}={_format_default(meta.default)}")
            lines.append("")

    if EXTRA_ENV_VARS:
        lines.append("")
        bar = "=" * (60 - len("non-registry"))
        lines.append(f"# === NON-REGISTRY {bar}")
        lines.append("# Env vars read directly via os.environ. Not in the config registry")
        lines.append("# because their shape (JSON, etc.) doesn't fit scalar typing.")
        lines.append("")
        for env_var, description, example in EXTRA_ENV_VARS:
            lines.append(f"# {description}")
            lines.append(f"# Example: {env_var}={example}")
            lines.append(f"# {env_var}=")
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    print(build_env_example_text(), end="")


if __name__ == "__main__":
    main()
