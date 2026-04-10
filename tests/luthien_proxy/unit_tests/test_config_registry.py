"""Tests for the unified config registry."""

from __future__ import annotations

import difflib
import importlib.util
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.config_fields import CONFIG_FIELDS, CONFIG_FIELDS_BY_NAME
from luthien_proxy.config_registry import (
    ConfigOverriddenError,
    ConfigRegistry,
    ConfigSource,
    ResolvedValue,
    coerce_value,
)
from luthien_proxy.settings import Settings


def _make_settings(**overrides: Any) -> Settings:
    defaults = {"database_url": "sqlite:///test.db"}
    defaults.update(overrides)
    return Settings(**defaults)


def _make_registry(
    settings: Settings | None = None,
    db_pool: Any = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ConfigRegistry:
    if settings is None:
        settings = _make_settings()
    return ConfigRegistry(settings=settings, db_pool=db_pool, cli_overrides=cli_overrides)


class TestResolveDefaults:
    def test_default_value_when_no_overrides(self):
        registry = _make_registry()
        registry._resolve_all()
        resolved = registry.get_resolved("gateway_port")
        assert resolved.value == 8000
        assert resolved.source == ConfigSource.DEFAULT

    def test_all_fields_resolve(self):
        registry = _make_registry()
        registry._resolve_all()
        for meta in CONFIG_FIELDS:
            resolved = registry.get_resolved(meta.name)
            assert resolved is not None
            assert isinstance(resolved, ResolvedValue)


class TestResolvePriority:
    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_PORT", "9000")
        settings = _make_settings(gateway_port=9000)
        registry = _make_registry(settings=settings)
        registry._resolve_all()

        resolved = registry.get_resolved("gateway_port")
        assert resolved.value == 9000
        assert resolved.source == ConfigSource.ENV

    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_PORT", "9000")
        settings = _make_settings(gateway_port=9000)
        registry = _make_registry(settings=settings, cli_overrides={"gateway_port": 7777})
        registry._resolve_all()

        resolved = registry.get_resolved("gateway_port")
        assert resolved.value == 7777
        assert resolved.source == ConfigSource.CLI

    def test_cli_overrides_db(self):
        registry = _make_registry(cli_overrides={"dogfood_mode": True})
        registry._db_values = {"dogfood_mode": "false"}
        registry._resolve_all()

        resolved = registry.get_resolved("dogfood_mode")
        assert resolved.value is True
        assert resolved.source == ConfigSource.CLI

    def test_db_overrides_default_for_db_settable(self):
        registry = _make_registry()
        registry._db_values = {"dogfood_mode": "true"}
        registry._resolve_all()

        resolved = registry.get_resolved("dogfood_mode")
        assert resolved.value is True
        assert resolved.source == ConfigSource.DB

    def test_db_ignored_for_non_db_settable(self):
        registry = _make_registry()
        registry._db_values = {"gateway_port": "9999"}
        registry._resolve_all()

        resolved = registry.get_resolved("gateway_port")
        assert resolved.value == 8000
        assert resolved.source == ConfigSource.DEFAULT

    def test_env_overrides_db(self, monkeypatch):
        monkeypatch.setenv("DOGFOOD_MODE", "false")
        settings = _make_settings(dogfood_mode=False)
        registry = _make_registry(settings=settings)
        registry._db_values = {"dogfood_mode": "true"}
        registry._resolve_all()

        resolved = registry.get_resolved("dogfood_mode")
        assert resolved.value is False
        assert resolved.source == ConfigSource.ENV


class TestOverridesTracking:
    def test_overrides_contains_lower_priority_sources(self, monkeypatch):
        monkeypatch.setenv("DOGFOOD_MODE", "true")
        settings = _make_settings(dogfood_mode=True)
        registry = _make_registry(settings=settings, cli_overrides={"dogfood_mode": False})
        registry._db_values = {"dogfood_mode": "true"}
        registry._resolve_all()

        resolved = registry.get_resolved("dogfood_mode")
        assert resolved.source == ConfigSource.CLI
        assert "env" in resolved.overrides
        assert "db" in resolved.overrides

    def test_no_overrides_when_only_default(self):
        registry = _make_registry()
        registry._resolve_all()
        resolved = registry.get_resolved("dogfood_mode")
        assert resolved.source == ConfigSource.DEFAULT
        assert resolved.overrides == {}


class TestLoadDbValues:
    @pytest.mark.asyncio
    async def test_orphan_keys_are_dropped(self, caplog):
        """Rows for removed/renamed fields must not enter _db_values."""
        import logging

        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"key": "dogfood_mode", "value": "true"},
                {"key": "this_field_does_not_exist", "value": '"stale"'},
            ]
        )
        mock_db = MagicMock()
        mock_db.get_pool = AsyncMock(return_value=mock_pool)

        registry = ConfigRegistry(settings=_make_settings(), db_pool=mock_db)
        with caplog.at_level(logging.WARNING):
            await registry._load_db_values()
        assert "dogfood_mode" in registry._db_values
        assert "this_field_does_not_exist" not in registry._db_values
        assert any("orphan" in rec.message.lower() or "removed" in rec.message.lower() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_db_error_logs_at_error_level_and_keeps_empty(self, caplog):
        """DB failures must log at ERROR level — fail-open is operator-hostile when silent."""
        import logging

        mock_db = MagicMock()
        mock_db.get_pool = AsyncMock(side_effect=RuntimeError("connection refused"))

        registry = ConfigRegistry(settings=_make_settings(), db_pool=mock_db)
        with caplog.at_level(logging.ERROR):
            await registry._load_db_values()
        assert registry._db_values == {}
        assert any(rec.levelno == logging.ERROR for rec in caplog.records)


class TestDashboardView:
    def test_dashboard_returns_all_fields(self):
        registry = _make_registry()
        registry._resolve_all()
        view = registry.dashboard_view()
        assert len(view) == len(CONFIG_FIELDS)

    def test_sensitive_fields_masked(self):
        registry = _make_registry()
        registry._resolve_all()
        view = registry.dashboard_view()
        for entry in view:
            if entry["sensitive"]:
                assert entry["value"] == "***"
                # Defaults must also be masked so a stored default credential
                # (latent today but plausible later) never leaks to the UI.
                assert entry["default"] == "***"

    def test_dashboard_entry_structure(self):
        registry = _make_registry()
        registry._resolve_all()
        view = registry.dashboard_view()
        entry = view[0]
        assert "name" in entry
        assert "env_var" in entry
        assert "category" in entry
        assert "description" in entry
        assert "value" in entry
        assert "source" in entry
        assert "default" in entry
        assert "db_settable" in entry
        assert "restart_required" in entry
        assert "sensitive" in entry
        assert "overrides" in entry


class TestSetDbValue:
    @pytest.mark.asyncio
    async def test_rejects_non_db_settable(self):
        registry = _make_registry()
        registry._resolve_all()
        with pytest.raises(ValueError, match="not DB-settable"):
            await registry.set_db_value("gateway_port", 9000)

    @pytest.mark.asyncio
    async def test_rejects_unknown_field(self):
        registry = _make_registry()
        registry._resolve_all()
        with pytest.raises(ValueError, match="Unknown"):
            await registry.set_db_value("nonexistent_field", True)

    @pytest.mark.asyncio
    async def test_rejects_without_db(self):
        registry = _make_registry()
        registry._resolve_all()
        with pytest.raises(ValueError, match="No database"):
            await registry.set_db_value("dogfood_mode", True)

    @pytest.mark.asyncio
    async def test_set_and_resolve(self):
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_db = MagicMock()
        mock_db.get_pool = AsyncMock(return_value=mock_pool)

        settings = _make_settings()
        registry = ConfigRegistry(settings=settings, db_pool=mock_db)
        registry._resolve_all()

        result = await registry.set_db_value("dogfood_mode", True)
        assert result.value is True
        assert result.source == ConfigSource.DB
        assert settings.dogfood_mode is True

    @pytest.mark.asyncio
    async def test_serializes_coerced_value_not_raw(self):
        """DB should store the canonical coerced value so raw strings like
        'true' become the JSON boolean true, not '"true"'."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_db = MagicMock()
        mock_db.get_pool = AsyncMock(return_value=mock_pool)

        settings = _make_settings()
        registry = ConfigRegistry(settings=settings, db_pool=mock_db)
        registry._resolve_all()

        await registry.set_db_value("dogfood_mode", "true")

        # The second positional arg to execute() is the serialized value.
        call_args = mock_pool.execute.call_args
        serialized = call_args.args[2]
        assert serialized == "true", f"expected canonical 'true', got {serialized!r}"
        assert registry._db_values["dogfood_mode"] == "true"

    @pytest.mark.asyncio
    async def test_delete_db_value(self):
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_db = MagicMock()
        mock_db.get_pool = AsyncMock(return_value=mock_pool)

        settings = _make_settings()
        registry = ConfigRegistry(settings=settings, db_pool=mock_db)
        registry._db_values = {"dogfood_mode": "true"}
        registry._resolve_all()
        assert registry.get("dogfood_mode") is True

        result = await registry.delete_db_value("dogfood_mode")
        assert result.source == ConfigSource.DEFAULT
        assert result.value is False

    @pytest.mark.asyncio
    async def test_set_raises_when_cli_override_present(self):
        """Under a CLI override, set_db_value must refuse the write."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_db = MagicMock()
        mock_db.get_pool = AsyncMock(return_value=mock_pool)

        settings = _make_settings()
        registry = ConfigRegistry(settings=settings, db_pool=mock_db, cli_overrides={"dogfood_mode": True})
        registry._resolve_all()
        with pytest.raises(ConfigOverriddenError) as exc:
            await registry.set_db_value("dogfood_mode", False)
        assert exc.value.source == ConfigSource.CLI
        mock_pool.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_raises_when_env_override_present(self, monkeypatch):
        """DELETE must also refuse when a higher layer is active, otherwise
        the user thinks they cleared the override when nothing observable changed."""
        mock_pool = MagicMock()
        mock_pool.execute = AsyncMock()
        mock_db = MagicMock()
        mock_db.get_pool = AsyncMock(return_value=mock_pool)

        monkeypatch.setenv("DOGFOOD_MODE", "true")
        settings = _make_settings(dogfood_mode=True)
        registry = ConfigRegistry(settings=settings, db_pool=mock_db)
        registry._db_values = {"dogfood_mode": "true"}
        registry._resolve_all()
        with pytest.raises(ConfigOverriddenError) as exc:
            await registry.delete_db_value("dogfood_mode")
        assert exc.value.source == ConfigSource.ENV
        mock_pool.execute.assert_not_called()


class TestCoerce:
    def test_bool_from_string_true(self):
        meta = CONFIG_FIELDS_BY_NAME["dogfood_mode"]
        assert coerce_value(meta, "true") is True
        assert coerce_value(meta, "1") is True
        assert coerce_value(meta, "yes") is True

    def test_bool_from_string_false(self):
        meta = CONFIG_FIELDS_BY_NAME["dogfood_mode"]
        assert coerce_value(meta, "false") is False
        assert coerce_value(meta, "0") is False
        assert coerce_value(meta, "no") is False

    def test_bool_from_json_string_db_path(self):
        """DB values arrive as JSON-encoded strings (json.dumps(True) == 'true')."""
        meta = CONFIG_FIELDS_BY_NAME["dogfood_mode"]
        assert coerce_value(meta, "true", from_db=True) is True

    def test_int_from_string(self):
        meta = CONFIG_FIELDS_BY_NAME["gateway_port"]
        assert coerce_value(meta, "9000") == 9000

    def test_int_from_json_string_db_path(self):
        # JSON-encoded form as stored in gateway_config.value (json.dumps(9000) = '9000').
        meta = CONFIG_FIELDS_BY_NAME["gateway_port"]
        import json as _json

        assert coerce_value(meta, _json.dumps(9000), from_db=True) == 9000

    def test_float_from_string(self):
        meta = CONFIG_FIELDS_BY_NAME["sentry_traces_sample_rate"]
        assert coerce_value(meta, "0.5") == 0.5

    def test_invalid_bool_raises(self):
        meta = CONFIG_FIELDS_BY_NAME["dogfood_mode"]
        with pytest.raises(ValueError, match="Invalid boolean"):
            coerce_value(meta, "tru")
        with pytest.raises(ValueError, match="Invalid boolean"):
            coerce_value(meta, "flase")

    def test_str_passthrough(self):
        meta = CONFIG_FIELDS_BY_NAME["log_level"]
        assert coerce_value(meta, "debug") == "debug"

    def test_user_string_not_json_decoded(self):
        """User/API input 'true' for a string field must not be JSON-decoded
        to bool and re-stringified to 'True'. Regression test for the bug where
        coerce_value('true') would produce 'True'."""
        meta = CONFIG_FIELDS_BY_NAME["log_level"]
        # Without from_db=True, a literal string stays literal.
        assert coerce_value(meta, "true") == "true"
        assert coerce_value(meta, "null") == "null"
        assert coerce_value(meta, "123") == "123"

    def test_db_value_is_json_decoded(self):
        """DB rows store JSON, so from_db=True must decode before typing."""
        meta = CONFIG_FIELDS_BY_NAME["dogfood_mode"]
        # json.dumps(True) == "true", round-trip must yield True.
        assert coerce_value(meta, "true", from_db=True) is True

    def test_none_allowed_for_nullable_field(self):
        # llm_judge_model has default=None, so None is valid.
        meta = CONFIG_FIELDS_BY_NAME["llm_judge_model"]
        assert coerce_value(meta, None) is None

    def test_none_rejected_for_non_nullable_field(self):
        # log_level has default="info", so None violates the contract.
        meta = CONFIG_FIELDS_BY_NAME["log_level"]
        with pytest.raises(TypeError, match="cannot be None"):
            coerce_value(meta, None)

    def test_enum_from_string(self):
        # auth_mode is typed as the AuthMode enum.
        from luthien_proxy.credential_manager import AuthMode

        meta = CONFIG_FIELDS_BY_NAME["auth_mode"]
        assert coerce_value(meta, "passthrough") is AuthMode.PASSTHROUGH
        assert coerce_value(meta, "both") is AuthMode.BOTH
        assert coerce_value(meta, AuthMode.PROXY_KEY) is AuthMode.PROXY_KEY

    def test_enum_invalid_value_raises(self):
        meta = CONFIG_FIELDS_BY_NAME["auth_mode"]
        with pytest.raises(ValueError, match="expected one of"):
            coerce_value(meta, "not_a_mode")


class TestConfigFieldsCompleteness:
    def test_settings_generated_from_config_fields(self):
        """Settings model has exactly the fields defined in CONFIG_FIELDS."""
        settings_fields = set(Settings.model_fields.keys())
        registry_fields = {f.name for f in CONFIG_FIELDS}
        assert settings_fields == registry_fields, (
            f"Settings/CONFIG_FIELDS mismatch. "
            f"Only in Settings: {settings_fields - registry_fields}, "
            f"Only in CONFIG_FIELDS: {registry_fields - settings_fields}"
        )

    def test_settings_defaults_match_config_fields(self):
        """Settings field defaults must match CONFIG_FIELDS — regenerate drift guard.

        Catches the case where config_fields.py changes a default but
        generate_settings.py hasn't been re-run. dynamic_default fields are
        skipped because their resolved value depends on the build environment.
        """
        for meta in CONFIG_FIELDS:
            if meta.dynamic_default:
                continue
            field_info = Settings.model_fields[meta.name]
            assert field_info.default == meta.default, (
                f"Settings.{meta.name}.default={field_info.default!r} "
                f"but CONFIG_FIELDS entry has default={meta.default!r}. "
                f"Run: uv run python scripts/generate_settings.py"
            )

    def test_no_duplicate_names(self):
        names = [f.name for f in CONFIG_FIELDS]
        assert len(names) == len(set(names)), f"Duplicate field names: {[n for n in names if names.count(n) > 1]}"

    def test_no_duplicate_env_vars(self):
        env_vars = [f.env_var for f in CONFIG_FIELDS]
        assert len(env_vars) == len(set(env_vars)), (
            f"Duplicate env vars: {[v for v in env_vars if env_vars.count(v) > 1]}"
        )

    def test_env_example_matches_generator(self):
        """The .env.example about to land in a commit must match the generator.

        Regression guard for PR #519: commit 1b28e4d5 accidentally wiped
        .env.example (150 lines -> 0), which broke CI clean-tree checks on main
        until it was spotted. The dev_checks.sh clean-tree check catches drift
        at pipeline end; this test adds a fast pytest-level guard and also
        catches the original "hardcoded SERVICE_VERSION=2.0.0" class of bug:
        any config field whose default drifts from config_fields.py fails here.

        Reads from the git index (`git show :.env.example`), NOT the working
        tree, for several reasons:

        - In CI and on a clean checkout, the index mirrors HEAD — the
          committed state we want to validate.
        - In dev_checks.sh, the pipeline regenerates .env.example into the
          working tree BEFORE the auto-stage step (which only stages files
          dirtied by `ruff format`/`ruff check --fix`). So the index is not
          updated and still reflects the committed state — a stale committed
          copy fails this test earlier than the end-of-pipeline clean-tree
          gate, giving the developer faster feedback.
        - In a pre-commit TDD workflow (dev edits config_fields.py,
          regenerates, stages, runs pytest), the index shows the staged
          version, avoiding a false-positive from comparing against stale HEAD.
        - Reading the raw working tree would miss the dev_checks.sh case
          because `dev_checks.sh` rewrites the working tree before pytest
          runs, making the comparison tautological.

        Note: reading the index isolates unstaged edits to `.env.example`
        itself, but NOT unstaged edits to `config_fields.py` — the generator
        imports the live module, so an unstaged config edit will still
        propagate through and the test will compare generator(working-tree
        config) vs index(.env.example). That's intentional: `config_fields.py`
        is the input under test.

        The generator intentionally omits dynamic_default values (like
        SERVICE_VERSION resolved from PROXY_VERSION) so its output is stable
        across build environments.
        """
        repo_root = Path(__file__).resolve().parents[3]
        generator_path = repo_root / "scripts" / "generate_env_example.py"

        assert generator_path.exists(), f"Generator missing at {generator_path}"

        # Load the script as a module to call build_env_example_text() in-process.
        # Faster than shelling out and produces real tracebacks on generator errors.
        spec = importlib.util.spec_from_file_location("_generate_env_example", generator_path)
        assert spec is not None and spec.loader is not None
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        expected = generator.build_env_example_text()

        # Read from the git index (`git show :path`), NOT the working tree.
        # For tracked unmodified files the index mirrors HEAD, so in CI and
        # clean checkouts this equals the committed state — the thing we want
        # to validate. For a dev mid-commit who has staged a regenerated copy,
        # it equals what's about to land. For a dev with unstaged working-tree
        # edits to .env.example, the index still reflects the last staged
        # state, so uncommitted WIP doesn't false-positive or false-negative
        # the test. This sidesteps the dev_checks.sh trap: dev_checks
        # regenerates the working tree then auto-stages it before pytest runs,
        # so the index is exactly the state that will be committed.
        result = subprocess.run(
            ["git", "show", ":.env.example"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            check=True,
        )
        actual = result.stdout

        if actual != expected:
            diff = "\n".join(
                difflib.unified_diff(
                    expected.splitlines(),
                    actual.splitlines(),
                    fromfile="expected (generator output)",
                    tofile="actual (git index)",
                    lineterm="",
                )
            )
            pytest.fail(
                "Staged/committed .env.example is out of sync with "
                "scripts/generate_env_example.py.\n"
                "Run: uv run python scripts/generate_env_example.py > .env.example "
                "&& git add .env.example\n\n"
                f"{diff}"
            )
