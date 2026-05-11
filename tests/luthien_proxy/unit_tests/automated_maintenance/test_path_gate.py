"""Unit tests for the autofix forbidden-paths gate."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from tests.luthien_proxy.unit_tests.automated_maintenance.conftest import (
    AUTOMATED_MAINTENANCE_LIB,
)

_PATH_GATE_PATH = AUTOMATED_MAINTENANCE_LIB / "path_gate.py"
_spec = importlib.util.spec_from_file_location("maint_path_gate", _PATH_GATE_PATH)
assert _spec is not None and _spec.loader is not None
path_gate = importlib.util.module_from_spec(_spec)
sys.modules["maint_path_gate"] = path_gate
_spec.loader.exec_module(path_gate)


@pytest.mark.parametrize(
    "path",
    [
        # Bare env files at the root or in subdirs.
        ".env",
        ".envrc",
        ".env.local",
        ".env.production",
        ".env.example",
        "config/.env",
        "config/.envrc",
        "config/.env.local",
        "deploy/staging/.env.staging",
        # `<name>.env` variants.
        "tests/fixtures/foo.env",
        "src/sample.env",
        "config/prod.env",
        # Migrations at any depth.
        "migrations/001_init.sql",
        "migrations/postgres/042_users.sql",
        "src/something/migrations/local.sql",
        # The pipeline itself.
        "scripts/automated_maintenance/automated_maintenance.sh",
        "scripts/automated_maintenance/lib/autofix.sh",
        "scripts/automated_maintenance/deploy/install.sh",
        # The maintenance pipeline's own tests.
        "tests/luthien_proxy/unit_tests/automated_maintenance/test_path_gate.py",
        "tests/luthien_proxy/unit_tests/automated_maintenance/__init__.py",
    ],
)
def test_blocks_sensitive_path(path: str) -> None:
    assert path_gate.is_forbidden(path), f"expected blocked: {path}"


@pytest.mark.parametrize(
    "path",
    [
        # Code under src/ (typical autofix target).
        "src/luthien_proxy/main.py",
        "src/luthien_proxy/policies/noop_policy.py",
        # Files outside the maintenance pipeline.
        "tests/luthien_proxy/unit_tests/test_auth.py",
        "tests/luthien_proxy/integration_tests/test_mock_anthropic_server.py",
        # Documentation.
        "README.md",
        "dev-README.md",
        "ARCHITECTURE.md",
        "dev/context/gotchas.md",
        # Top-level scripts NOT under automated_maintenance/.
        "scripts/dev_checks.sh",
        "scripts/run_e2e.sh",
        "scripts/start_gateway.sh",
        # Files that LOOK like env but aren't — must not over-block.
        "src/luthien_proxy/envoy.py",
        "src/luthien_proxy/environment.py",
        "tests/test_envoyhandler.py",
        # `.envrc` lookalikes that should pass.
        "src/foo_envrc.py",
        # Codegen helpers.
        "scripts/generate_env_example.py",
        "config/policy_config.yaml",
    ],
)
def test_allows_safe_path(path: str) -> None:
    assert not path_gate.is_forbidden(path), f"expected allowed: {path}"


def test_classify_returns_only_blocked():
    paths = [
        "src/luthien_proxy/main.py",
        ".env.local",
        "tests/luthien_proxy/unit_tests/automated_maintenance/test_x.py",
        "README.md",
        "migrations/099_add_col.sql",
    ]
    blocked = path_gate.classify(paths)
    assert set(blocked) == {
        ".env.local",
        "tests/luthien_proxy/unit_tests/automated_maintenance/test_x.py",
        "migrations/099_add_col.sql",
    }


def test_classify_empty_input():
    assert path_gate.classify([]) == []


def test_main_exits_zero_on_safe_paths(capsys):
    rc = path_gate.main(["src/foo.py", "README.md"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_main_exits_two_and_prints_blocked(capsys):
    rc = path_gate.main(["src/foo.py", ".env", "migrations/x.sql"])
    assert rc == 2
    captured = capsys.readouterr()
    assert ".env" in captured.out
    assert "migrations/x.sql" in captured.out
    assert "src/foo.py" not in captured.out
