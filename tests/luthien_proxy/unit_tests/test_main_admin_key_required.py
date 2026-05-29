# ABOUTME: create_app refuses to boot an unauthenticated admin surface
# ABOUTME: ADMIN_API_KEY unset + localhost bypass disabled => RuntimeError at construction

"""Startup invariant: the admin surface must not be served unauthenticated.

`create_app` raises if `ADMIN_API_KEY` is unset while `LOCALHOST_AUTH_BYPASS`
is disabled (a network-exposed deployment). This is defense-in-depth: shipped
entry points already avoid the state via `auto_provision_defaults()`, but the
invariant is pinned at the factory so any other entry point is covered too.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from luthien_proxy.main import create_app
from luthien_proxy.settings import clear_settings_cache


@pytest.fixture
def clean_settings() -> Iterator[None]:
    """Ensure get_settings() reflects per-test env, before and after."""
    clear_settings_cache()
    yield
    clear_settings_cache()


def _build(monkeypatch: pytest.MonkeyPatch, *, admin_key: str | None, bypass: bool) -> FastAPI:
    monkeypatch.setenv("LOCALHOST_AUTH_BYPASS", "true" if bypass else "false")
    clear_settings_cache()
    return create_app(
        api_key="client-key",
        admin_key=admin_key,
        db_pool=MagicMock(),
        redis_client=None,
    )


def test_refuses_to_boot_without_admin_key_when_bypass_disabled(
    monkeypatch: pytest.MonkeyPatch, clean_settings: None
) -> None:
    with pytest.raises(RuntimeError, match="ADMIN_API_KEY"):
        _build(monkeypatch, admin_key=None, bypass=False)


def test_boots_without_admin_key_when_bypass_enabled(monkeypatch: pytest.MonkeyPatch, clean_settings: None) -> None:
    app = _build(monkeypatch, admin_key=None, bypass=True)
    assert isinstance(app, FastAPI)


def test_boots_with_admin_key_when_bypass_disabled(monkeypatch: pytest.MonkeyPatch, clean_settings: None) -> None:
    app = _build(monkeypatch, admin_key="admin-key", bypass=False)
    assert isinstance(app, FastAPI)
