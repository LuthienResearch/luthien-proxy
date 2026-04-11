"""Regression tests for boot_sqlite_gateway's setup-failure cleanup.

These verify the contract added by the ExitStack rewrite: a raise from any
setup step (check_migrations, create_app, the gateway-startup wait) must
still tear down everything that was set up.
"""

from __future__ import annotations

import os

import pytest
from tests.luthien_proxy.e2e_tests.sqlite import _boot
from tests.luthien_proxy.e2e_tests.sqlite._boot import boot_sqlite_gateway

pytestmark = pytest.mark.sqlite_e2e


def _existing_tmp_dirs(prefix: str) -> set[str]:
    import tempfile

    root = tempfile.gettempdir()
    return {name for name in os.listdir(root) if name.startswith(prefix)}


def test_create_app_failure_cleans_up_tmp_dir_and_env(monkeypatch):
    """If create_app raises, tmp_dir is removed and env vars are restored."""
    prefix = "luthien_boot_helper_test_create_app_"

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://sentinel.invalid")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        _boot,
        "create_app",
        lambda **_: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    before = _existing_tmp_dirs(prefix)

    with pytest.raises(RuntimeError, match="boom"):
        with boot_sqlite_gateway(
            api_key="k",
            admin_key="a",
            mock_anthropic_url="http://127.0.0.1:1",
            tmp_prefix=prefix,
            thread_name="boot-helper-test",
        ):
            pytest.fail("yield should not be reached")

    after = _existing_tmp_dirs(prefix)
    assert after == before, f"tmp_dir leaked: {after - before}"
    assert os.environ["ANTHROPIC_BASE_URL"] == "http://sentinel.invalid"
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_check_migrations_failure_cleans_up(monkeypatch):
    """If check_migrations raises, tmp_dir is removed."""
    prefix = "luthien_boot_helper_test_migrations_"

    async def boom(_pool):
        raise RuntimeError("migrations failed")

    monkeypatch.setattr(_boot, "check_migrations", boom)

    before = _existing_tmp_dirs(prefix)

    with pytest.raises(RuntimeError, match="migrations failed"):
        with boot_sqlite_gateway(
            api_key="k",
            admin_key="a",
            mock_anthropic_url="http://127.0.0.1:1",
            tmp_prefix=prefix,
            thread_name="boot-helper-test",
        ):
            pytest.fail("yield should not be reached")

    after = _existing_tmp_dirs(prefix)
    assert after == before, f"tmp_dir leaked: {after - before}"
