"""Guardrails for the auto-generated ``.env.example`` file.

These tests prevent three failure modes seen in recent history:

1. ``.env.example`` getting committed empty (regression from a merge/stage
   mistake). dev_checks.sh regenerates it and the clean-tree check is
   supposed to catch drift, but staging the wrong version still slipped
   through once — assert the committed file is non-trivial.

2. ``generate_env_example.py`` silently degrading (e.g. imports failing or
   field enumeration returning nothing) so the generator outputs nothing.

3. Enum defaults leaking their repr (e.g. ``AUTH_MODE=AuthMode.BOTH``)
   instead of their ``.value`` (``auth_mode=both``). Any shell sourcing the
   file would set the literal string ``AuthMode.BOTH`` as the env var.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
GENERATOR = REPO_ROOT / "scripts" / "generate_env_example.py"


def test_env_example_file_is_non_empty() -> None:
    """The committed ``.env.example`` must not be empty.

    A previous PR committed a zero-byte ``.env.example``; this test exists so
    the next time that happens, the unit suite catches it instead of users.
    """
    assert ENV_EXAMPLE.exists(), ".env.example must exist at repo root"
    content = ENV_EXAMPLE.read_text()
    assert content.strip(), ".env.example is empty — run `uv run python scripts/generate_env_example.py > .env.example`"
    # Cheap sanity: at least a few auth-related fields should be present.
    for key in ("PROXY_API_KEY", "ADMIN_API_KEY", "ANTHROPIC_API_KEY"):
        assert key in content, f".env.example is missing an entry for {key}"


def test_generator_produces_non_empty_output() -> None:
    """Running the generator directly must produce substantial output.

    Runs the script in a subprocess to match what dev_checks.sh does, and
    guards against silent breakage (e.g. import errors that would surface
    only when CI runs dev_checks).
    """
    result = subprocess.run(
        [sys.executable, str(GENERATOR)],
        capture_output=True,
        text=True,
        check=True,
    )
    output = result.stdout
    assert output, "generate_env_example.py produced empty output"
    # 150+ lines is current size; allow headroom but catch total collapse.
    assert len(output.splitlines()) > 50, (
        "generate_env_example.py produced suspiciously short output — did CONFIG_FIELDS break?"
    )


def test_generator_does_not_leak_enum_repr() -> None:
    """Enum defaults must render as their value, not the Python repr.

    Before this test existed, ``AUTH_MODE`` rendered as ``AuthMode.BOTH`` in
    ``.env.example``. A user who uncommented and sourced it would set
    ``AUTH_MODE=AuthMode.BOTH`` literally — which doesn't parse as the
    ``AuthMode`` enum and the gateway would fail on startup.
    """
    result = subprocess.run(
        [sys.executable, str(GENERATOR)],
        capture_output=True,
        text=True,
        check=True,
    )
    output = result.stdout
    # Any ``ClassName.MEMBER`` default in a commented env line is bad.
    # We check specifically for the observed AuthMode case plus a generic
    # pattern guard so future enum fields also get caught.
    forbidden_snippets = ["AuthMode.", "Enum.", "IntEnum."]
    offenders = [snippet for snippet in forbidden_snippets if snippet in output]
    assert not offenders, (
        f"generate_env_example.py emitted raw enum repr(s) {offenders!r}; use `meta.default.value` for enum defaults."
    )
