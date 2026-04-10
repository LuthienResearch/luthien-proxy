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

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
GENERATOR = REPO_ROOT / "scripts" / "generate_env_example.py"


@pytest.fixture(scope="module")
def generator_output() -> str:
    """Run generate_env_example.main() in-process and return stdout.

    Imports the script as a module rather than spawning a subprocess so the
    test stays inside the unit-test perf budget (<0.05s). The script's
    sys.path side effect is a one-time mutation and safe to leave in place
    for the rest of the test session.
    """
    import io

    spec = importlib.util.spec_from_file_location("_env_example_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buf = io.StringIO()
    stdout_saved = sys.stdout
    try:
        sys.stdout = buf
        module.main()
    finally:
        sys.stdout = stdout_saved
    return buf.getvalue()


def test_env_example_file_is_non_empty() -> None:
    """The committed ``.env.example`` must not be empty.

    A previous PR committed a zero-byte ``.env.example``; this test exists so
    the next time that happens, the unit suite catches it instead of users.
    """
    assert ENV_EXAMPLE.exists(), ".env.example must exist at repo root"
    content = ENV_EXAMPLE.read_text()
    assert content.strip(), ".env.example is empty — run `uv run python scripts/generate_env_example.py > .env.example`"
    # Cheap sanity: all three auth-related fields plus AUTH_MODE should be present.
    for key in ("PROXY_API_KEY", "ADMIN_API_KEY", "ANTHROPIC_API_KEY", "AUTH_MODE"):
        assert key in content, f".env.example is missing an entry for {key}"


def test_generator_produces_non_empty_output(generator_output: str) -> None:
    """Running the generator must produce substantial output.

    Guards against silent breakage (e.g. import errors that would surface
    only when CI runs dev_checks).
    """
    assert generator_output, "generate_env_example.py produced empty output"
    # 150+ lines is current size; allow headroom but catch total collapse.
    assert len(generator_output.splitlines()) > 50, (
        "generate_env_example.py produced suspiciously short output — did CONFIG_FIELDS break?"
    )


def test_generator_does_not_leak_enum_repr(generator_output: str) -> None:
    """Enum defaults must render as their value, not the Python repr.

    Before this test existed, ``AUTH_MODE`` rendered as ``AuthMode.BOTH`` in
    ``.env.example``. A user who uncommented and sourced it would set
    ``AUTH_MODE=AuthMode.BOTH`` literally — which doesn't parse as the
    ``AuthMode`` enum and the gateway would fail on startup.
    """
    # Positive assertion: the actual intended output.
    assert "# AUTH_MODE=both" in generator_output, (
        "Expected '# AUTH_MODE=both' in generator output — did the AuthMode enum default change?"
    )

    # Negative assertion: no assignment line looks like `# VAR=ClassName.MEMBER`.
    # Anchored to assignment lines so a prose description mentioning "Enum."
    # in a field comment doesn't false-positive. Matches commented env var
    # lines where the value starts with a capital-letter Python identifier
    # followed by a dotted attribute — the signature of an enum repr.
    enum_repr_pattern = re.compile(r"^# [A-Z][A-Z0-9_]*=[A-Z][A-Za-z0-9_]*\.[A-Z_][A-Za-z0-9_]*$", re.MULTILINE)
    offenders = enum_repr_pattern.findall(generator_output)
    assert not offenders, (
        f"generate_env_example.py emitted raw enum repr lines {offenders!r}; "
        "add an Enum branch to `_format_default` in scripts/generate_env_example.py."
    )
