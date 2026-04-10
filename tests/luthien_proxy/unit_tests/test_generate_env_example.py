"""Unit tests for the .env.example generator.

The generator converts ConfigFieldMeta defaults into env-var strings that
must round-trip through the env parser. Bools render lowercase, enums
render as their .value (not their repr), None renders as empty, and
dynamic_default fields render as a blank with a comment.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType

import pytest

from luthien_proxy.config_fields import CONFIG_FIELDS

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_env_example.py"


def _load_generator() -> ModuleType:
    """Import scripts/generate_env_example.py as an in-process module.

    The script lives outside the package tree, so we import it by file path
    rather than via a module name. This gives us direct access to `main()`
    and real code coverage, without shelling out.
    """
    spec = importlib.util.spec_from_file_location("_generate_env_example", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_generate_env_example"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generated_output() -> str:
    """Run the generator in-process and capture its stdout."""
    module = _load_generator()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        module.main()
    return buf.getvalue()


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
        assert "# LOCALHOST_AUTH_BYPASS=True" not in generated_output
        assert "# DOGFOOD_MODE=false" in generated_output
        assert "# DOGFOOD_MODE=False" not in generated_output

    def test_none_default_renders_empty(self, generated_output: str) -> None:
        """Fields with no default render as bare `# FIELD=` — no literal `None`."""
        assert "# PROXY_API_KEY=" in generated_output
        assert "# PROXY_API_KEY=None" not in generated_output

    def test_int_default_renders_as_decimal(self, generated_output: str) -> None:
        """Int defaults like GATEWAY_PORT=8000 render as plain decimals."""
        assert "# GATEWAY_PORT=8000" in generated_output

    def test_dynamic_default_renders_blank_with_comment(self, generated_output: str) -> None:
        """dynamic_default fields (e.g. SERVICE_VERSION) must render as a
        blank value with a "derived from <SYMBOL> at startup" comment.

        Baking the resolved value into .env.example would make the generator
        non-deterministic across build environments. This was fixed in
        commit 533f5fc0.
        """
        assert "(default derived from PROXY_VERSION at startup)" in generated_output
        assert "# SERVICE_VERSION=" in generated_output
        lines = generated_output.splitlines()
        service_version_lines = [line for line in lines if line.startswith("# SERVICE_VERSION")]
        assert any(line == "# SERVICE_VERSION=" for line in service_version_lines), (
            f"expected blank SERVICE_VERSION assignment, saw {service_version_lines!r}"
        )

    def test_header_present(self, generated_output: str) -> None:
        """The generated file must be recognizable as auto-generated."""
        assert "Auto-generated from config field definitions" in generated_output

    def test_all_enum_defaults_round_trip(self, generated_output: str) -> None:
        """Every enum-valued config field must render as its .value.

        This is the real contract: the string we write must be something
        Settings's env parser would accept back. A parametrized scan over
        CONFIG_FIELDS catches future enum additions that re-introduce the
        same bug.
        """
        enum_fields = [
            meta
            for meta in CONFIG_FIELDS
            if meta.default is not None and isinstance(meta.default, Enum) and not meta.dynamic_default
        ]
        assert enum_fields, "test harness expected at least one enum field; update if none remain"
        for meta in enum_fields:
            expected = f"# {meta.env_var}={meta.default.value}"
            assert expected in generated_output, f"enum field {meta.env_var} rendered wrong; expected {expected!r}"
