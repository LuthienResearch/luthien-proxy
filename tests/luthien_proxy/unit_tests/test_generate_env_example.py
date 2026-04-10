"""Unit tests for the .env.example generator.

The generator converts ConfigFieldMeta defaults into env-var strings that
must round-trip through the env parser. Bools render lowercase, enums
render as their .value (not their repr), and None renders as empty.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "generate_env_example.py"


@pytest.fixture(scope="module")
def generated_output() -> str:
    """Run the generator once and share the output across tests."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    )
    return result.stdout


class TestGenerateEnvExample:
    """The generator must produce env values the parser can round-trip."""

    def test_enum_default_renders_as_value_not_repr(self, generated_output: str) -> None:
        """AuthMode.BOTH must render as `both`, not `AuthMode.BOTH`.

        Regression: previously `str(AuthMode.BOTH)` was written verbatim,
        producing `AUTH_MODE=AuthMode.BOTH` which fails enum coercion when
        a user uncomments it.
        """
        assert "# AUTH_MODE=both" in generated_output
        assert "AuthMode.BOTH" not in generated_output

    def test_bool_default_renders_lowercase(self, generated_output: str) -> None:
        """Python bool `True`/`False` must render as `true`/`false`."""
        assert "# LOCALHOST_AUTH_BYPASS=true" in generated_output
        assert "True" not in generated_output.split("# LOCALHOST_AUTH_BYPASS=")[1].split("\n")[0]

    def test_none_default_renders_empty(self, generated_output: str) -> None:
        """Fields with no default render as bare `# FIELD=` — no literal `None`."""
        # PROXY_API_KEY has default=None and should render with an empty value.
        assert "# PROXY_API_KEY=" in generated_output
        # "# PROXY_API_KEY=None" would be wrong.
        assert "# PROXY_API_KEY=None" not in generated_output

    def test_int_default_renders_as_decimal(self, generated_output: str) -> None:
        """Int defaults like GATEWAY_PORT=8000 render as plain decimals."""
        assert "# GATEWAY_PORT=8000" in generated_output

    def test_header_present(self, generated_output: str) -> None:
        """The generated file must be recognizable as auto-generated."""
        assert "Auto-generated from config field definitions" in generated_output
