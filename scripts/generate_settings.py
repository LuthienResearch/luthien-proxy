#!/usr/bin/env python3
"""Generate settings.py from config_fields.py.

Produces a Settings class with explicit typed fields so Pyright can check
attribute access, while keeping config_fields.py as the single source of truth.

Usage:
    uv run python scripts/generate_settings.py          # write settings.py
    uv run python scripts/generate_settings.py --check   # exit 1 if stale
"""

import subprocess
import sys
from enum import Enum
from pathlib import Path

# Add src to path so we can import config_fields
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from luthien_proxy.config_fields import CONFIG_CATEGORIES, CONFIG_FIELDS

TARGET = Path(__file__).resolve().parent.parent / "src" / "luthien_proxy" / "settings.py"


def _type_annotation(meta):
    """Get the type annotation string for a field."""
    name = meta.field_type.__name__
    if meta.default is None:
        return f"{name} | None"
    return name


def _default_repr(meta):
    """Get the default value as source code."""
    # Dynamic defaults: emit the symbol name so the generated file re-evaluates
    # at import time (e.g. PROXY_VERSION from package metadata).
    if meta.default_from is not None:
        return meta.default_from[1]
    d = meta.default
    if d is None:
        return "None"
    if isinstance(d, Enum):
        return f"{type(d).__name__}.{d.name}"
    return repr(d)


def _collect_imports(fields):
    """Collect non-builtin type imports needed by field definitions.

    Covers both field_type imports (e.g. AuthMode) and default_from imports
    (e.g. PROXY_VERSION from luthien_proxy.version).
    """
    imports: dict[str, set[str]] = {}
    for meta in fields:
        mod = meta.field_type.__module__
        if mod != "builtins":
            imports.setdefault(mod, set()).add(meta.field_type.__name__)
        if meta.default_from is not None:
            mod_name, sym = meta.default_from
            imports.setdefault(mod_name, set()).add(sym)
    return imports


def generate() -> str:
    extra_imports = _collect_imports(CONFIG_FIELDS)
    # The legacy-AUTH_MODE tolerance validator needs parse_auth_mode alongside AuthMode.
    extra_imports.setdefault("luthien_proxy.credential_manager", set()).add("parse_auth_mode")

    fields_by_cat: dict[str, list] = {}
    for meta in CONFIG_FIELDS:
        fields_by_cat.setdefault(meta.category, []).append(meta)

    lines: list[str] = []

    # Header
    lines.append('"""Application settings — generated from config_fields.py.')
    lines.append("")
    lines.append("DO NOT EDIT BY HAND. Regenerate with:")
    lines.append("    uv run python scripts/generate_settings.py")
    lines.append("")
    lines.append("The Settings model is a plain class with explicit typed fields so Pyright")
    lines.append("can check attribute access. config_fields.py remains the single source of truth.")
    lines.append('"""')
    lines.append("")
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from functools import lru_cache")
    lines.append("")
    lines.append("from pydantic import field_validator, model_validator")
    lines.append("from pydantic_settings import BaseSettings, SettingsConfigDict")
    if extra_imports:
        lines.append("")
        for mod, names in sorted(extra_imports.items()):
            lines.append(f"from {mod} import {', '.join(sorted(names))}")
    lines.append("")
    lines.append("")

    # _SettingsBase
    lines.append("class _SettingsBase(BaseSettings):")
    lines.append('    """Base with pydantic-settings configuration. Fields declared on Settings."""')
    lines.append("")
    lines.append("    model_config = SettingsConfigDict(")
    lines.append('        env_file=".env",')
    lines.append('        env_file_encoding="utf-8",')
    lines.append('        extra="ignore",')
    lines.append("    )")
    lines.append("")
    lines.append('    @model_validator(mode="after")')
    lines.append('    def _set_environment_from_railway(self) -> "_SettingsBase":')
    lines.append('        """Auto-set environment from Railway service name."""')
    lines.append('        railway = getattr(self, "railway_service_name", "")')
    lines.append('        env = getattr(self, "environment", "development")')
    lines.append('        if railway and env == "development":')
    lines.append('            object.__setattr__(self, "environment", railway)')
    lines.append("        return self")
    lines.append("")
    # Legacy AUTH_MODE tolerance: intercept pre-#524 values (e.g. 'proxy_key')
    # BEFORE pydantic's enum coercer raises a ValidationError. Runs at module-
    # import time via get_settings(), so the gateway boots with a warning
    # instead of crash-looping on the old value. See parse_auth_mode for the
    # removal tracker.
    lines.append('    @field_validator("auth_mode", mode="before", check_fields=False)')
    lines.append("    @classmethod")
    lines.append("    def _coerce_legacy_auth_mode(cls, raw: object) -> object:")
    lines.append('        """Coerce pre-PR-#535 AUTH_MODE aliases (e.g. \'proxy_key\') before enum validation."""')
    lines.append("        if isinstance(raw, str):")
    lines.append("            try:")
    lines.append('                return parse_auth_mode(raw, source="AUTH_MODE env var").value')
    lines.append("            except ValueError:")
    lines.append("                # Let pydantic's enum validator produce the canonical error message.")
    lines.append("                return raw")
    lines.append("        return raw")
    lines.append("")
    lines.append("")

    # Settings class
    lines.append("class Settings(_SettingsBase):")
    lines.append('    """Application settings — all fields generated from config_fields.py."""')

    for cat in CONFIG_CATEGORIES:
        cat_fields = fields_by_cat.get(cat, [])
        if not cat_fields:
            continue
        lines.append("")
        bar = "\u2500" * (60 - len(cat))
        lines.append(f"    # \u2500\u2500 {cat} {bar}")
        for meta in cat_fields:
            ann = _type_annotation(meta)
            default = _default_repr(meta)
            lines.append(f"    {meta.name}: {ann} = {default}")

    lines.append("")
    lines.append("")

    # Helper functions
    lines.append("@lru_cache")
    lines.append("def get_settings() -> Settings:")
    lines.append('    """Get cached application settings."""')
    lines.append("    return Settings()")
    lines.append("")
    lines.append("")
    lines.append("def clear_settings_cache() -> None:")
    lines.append('    """Clear the settings cache. Useful for testing."""')
    lines.append("    get_settings.cache_clear()")
    lines.append("")
    lines.append("")
    lines.append('def client_error_detail(verbose_detail: str, generic_detail: str = "Internal server error") -> str:')
    lines.append('    """Pick the client-facing error message based on VERBOSE_CLIENT_ERRORS."""')
    lines.append("    return verbose_detail if get_settings().verbose_client_errors else generic_detail")
    lines.append("")
    lines.append("")
    lines.append('__all__ = ["Settings", "get_settings", "clear_settings_cache", "client_error_detail"]')
    lines.append("")

    return "\n".join(lines)


def _format_with_ruff(source: str) -> str:
    """Pipe source through `ruff format` so generator output matches repo style."""
    result = subprocess.run(
        ["uv", "run", "ruff", "format", "--stdin-filename", "settings.py", "-"],
        input=source,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def main() -> None:
    output = _format_with_ruff(generate())

    if "--check" in sys.argv:
        if not TARGET.exists():
            print(f"FAIL: {TARGET} does not exist")
            sys.exit(1)
        current = TARGET.read_text()
        if current != output:
            print(f"FAIL: {TARGET} is out of date. Run: uv run python scripts/generate_settings.py")
            sys.exit(1)
        print(f"OK: {TARGET} is up to date.")
        return

    TARGET.write_text(output)
    print(f"Generated {TARGET}")


if __name__ == "__main__":
    main()
