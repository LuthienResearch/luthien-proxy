"""Unit tests for `InferenceProviderRegistry`.

Covers dispatch, cache behavior, and DB round-trip against a real
in-memory SQLite with the #014 migration applied (so we're not asserting
against our own hand-rolled schema stub).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.credential_manager import CredentialManager
from luthien_proxy.credentials.credential import Credential, CredentialError, CredentialType
from luthien_proxy.inference.base import InferenceProvider, InferenceResult
from luthien_proxy.inference.registry import (
    InferenceProviderRegistry,
    MissingCredentialError,
    ProviderNotFoundError,
    ProviderRecord,
    UnknownBackendTypeError,
)
from luthien_proxy.utils.db import DatabasePool


class _StubProvider(InferenceProvider):
    """Minimal provider impl for isolating registry behavior."""

    backend_type = "stub"

    def __init__(self, *, name: str, record: ProviderRecord, credential: Credential | None) -> None:
        super().__init__(name=name)
        self.record = record
        self.credential = credential
        self.close_calls = 0

    async def complete(self, *args, **kwargs) -> InferenceResult:  # noqa: ANN003
        return InferenceResult.from_text("stub")

    async def close(self) -> None:
        self.close_calls += 1


def _stub_factory(record: ProviderRecord, credential: object) -> InferenceProvider:
    cred = credential if isinstance(credential, Credential) else None
    return _StubProvider(name=record.name, record=record, credential=cred)


# --- Fixtures ---


@pytest.fixture
async def sqlite_pool() -> DatabasePool:
    """Real in-memory SQLite with every migration applied."""
    pool = DatabasePool("sqlite://:memory:")
    migrations_dir = Path(__file__).resolve().parents[4] / "migrations" / "sqlite"

    async with pool.connection() as conn:
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            sql = migration_file.read_text()
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement and not all(
                    line.strip().startswith("--") or not line.strip() for line in statement.split("\n")
                ):
                    await conn.execute(statement)

    yield pool
    await pool.close()


@pytest.fixture
def mock_credential_manager() -> MagicMock:
    """Credential manager stub — only `_get_server_key` is used by the registry."""
    cm = MagicMock(spec=CredentialManager)
    cm._get_server_key = AsyncMock()
    return cm


@pytest.fixture
async def registry(sqlite_pool: DatabasePool, mock_credential_manager: MagicMock) -> InferenceProviderRegistry:
    """Registry with a stub factory so we don't construct real providers."""
    r = InferenceProviderRegistry(
        db_pool=sqlite_pool,
        credential_manager=mock_credential_manager,
        factories={"stub": _stub_factory},
    )
    await r.initialize()
    return r


# --- Tests ---


@pytest.mark.asyncio
async def test_list_empty_when_no_providers(registry: InferenceProviderRegistry) -> None:
    assert await registry.list() == []


@pytest.mark.asyncio
async def test_put_then_list_roundtrips_fields(registry: InferenceProviderRegistry) -> None:
    record = ProviderRecord(
        name="judge-one",
        backend_type="stub",
        credential_name="judge-cred",
        default_model="claude-sonnet-4-6",
        config={"timeout_seconds": 30},
    )
    await registry.put(record)

    listed = await registry.list()
    assert len(listed) == 1
    got = listed[0]
    assert got.name == "judge-one"
    assert got.backend_type == "stub"
    assert got.credential_name == "judge-cred"
    assert got.default_model == "claude-sonnet-4-6"
    assert got.config == {"timeout_seconds": 30}
    assert got.created_at is not None
    assert got.updated_at is not None


@pytest.mark.asyncio
async def test_put_is_upsert(registry: InferenceProviderRegistry) -> None:
    await registry.put(
        ProviderRecord(
            name="p1",
            backend_type="stub",
            credential_name=None,
            default_model="m1",
            config={},
        )
    )
    await registry.put(
        ProviderRecord(
            name="p1",
            backend_type="stub",
            credential_name=None,
            default_model="m2",
            config={"k": "v"},
        )
    )
    listed = await registry.list()
    assert len(listed) == 1
    assert listed[0].default_model == "m2"
    assert listed[0].config == {"k": "v"}


@pytest.mark.asyncio
async def test_delete_returns_true_when_row_existed(registry: InferenceProviderRegistry) -> None:
    await registry.put(
        ProviderRecord(
            name="p1",
            backend_type="stub",
            credential_name=None,
            default_model="m",
            config={},
        )
    )
    assert await registry.delete("p1") is True
    assert await registry.delete("p1") is False


@pytest.mark.asyncio
async def test_get_raises_when_missing(registry: InferenceProviderRegistry) -> None:
    with pytest.raises(ProviderNotFoundError):
        await registry.get("nope")


@pytest.mark.asyncio
async def test_get_dispatches_on_backend_type(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    cred = Credential(value="x", credential_type=CredentialType.API_KEY)
    mock_credential_manager._get_server_key = AsyncMock(return_value=cred)

    await registry.put(
        ProviderRecord(
            name="p",
            backend_type="stub",
            credential_name="c",
            default_model="m",
            config={},
        )
    )

    provider = await registry.get("p")
    assert isinstance(provider, _StubProvider)
    assert provider.credential is cred
    mock_credential_manager._get_server_key.assert_awaited_once_with("c")


@pytest.mark.asyncio
async def test_get_raises_for_unknown_backend_type(registry: InferenceProviderRegistry) -> None:
    # Skip registry.put()'s validation by writing directly to DB with a bogus
    # backend_type — simulates a row from a newer proxy whose backend has
    # since been removed.
    pool = await registry._db_pool.get_pool()  # type: ignore[union-attr]
    await pool.execute(
        "INSERT INTO inference_providers (name, backend_type, credential_name, "
        "default_model, config) VALUES ($1, $2, $3, $4, $5)",
        "rogue",
        "nonexistent_backend",
        None,
        "m",
        "{}",
    )
    with pytest.raises(UnknownBackendTypeError):
        await registry.get("rogue")


@pytest.mark.asyncio
async def test_get_raises_on_missing_credential(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    mock_credential_manager._get_server_key = AsyncMock(side_effect=CredentialError("Server key 'gone' not found"))
    await registry.put(
        ProviderRecord(
            name="p",
            backend_type="stub",
            credential_name="gone",
            default_model="m",
            config={},
        )
    )
    with pytest.raises(MissingCredentialError) as exc:
        await registry.get("p")
    # Soft-FK error names the provider AND the missing credential.
    assert "p" in str(exc.value)
    assert "gone" in str(exc.value)


@pytest.mark.asyncio
async def test_get_caches_within_ttl(registry: InferenceProviderRegistry, mock_credential_manager: MagicMock) -> None:
    cred = Credential(value="x", credential_type=CredentialType.API_KEY)
    mock_credential_manager._get_server_key = AsyncMock(return_value=cred)
    await registry.put(
        ProviderRecord(
            name="p",
            backend_type="stub",
            credential_name="c",
            default_model="m",
            config={},
        )
    )

    first = await registry.get("p")
    second = await registry.get("p")
    assert first is second
    # Only one credential resolution even though we called get() twice.
    assert mock_credential_manager._get_server_key.await_count == 1


@pytest.mark.asyncio
async def test_put_invalidates_cache_and_closes_old_instance(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    mock_credential_manager._get_server_key = AsyncMock(return_value=None)
    await registry.put(
        ProviderRecord(
            name="p",
            backend_type="stub",
            credential_name=None,
            default_model="m1",
            config={},
        )
    )
    first = await registry.get("p")
    assert isinstance(first, _StubProvider)

    await registry.put(
        ProviderRecord(
            name="p",
            backend_type="stub",
            credential_name=None,
            default_model="m2",
            config={},
        )
    )
    second = await registry.get("p")
    assert second is not first
    assert first.close_calls == 1


@pytest.mark.asyncio
async def test_delete_invalidates_cache(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    mock_credential_manager._get_server_key = AsyncMock(return_value=None)
    await registry.put(
        ProviderRecord(
            name="p",
            backend_type="stub",
            credential_name=None,
            default_model="m",
            config={},
        )
    )
    provider = await registry.get("p")
    assert isinstance(provider, _StubProvider)

    await registry.delete("p")
    assert provider.close_calls == 1

    with pytest.raises(ProviderNotFoundError):
        await registry.get("p")


@pytest.mark.asyncio
async def test_close_drains_cached_providers(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    mock_credential_manager._get_server_key = AsyncMock(return_value=None)
    for name in ("a", "b"):
        await registry.put(
            ProviderRecord(
                name=name,
                backend_type="stub",
                credential_name=None,
                default_model="m",
                config={},
            )
        )
    providers = [await registry.get("a"), await registry.get("b")]
    await registry.close()
    for p in providers:
        assert isinstance(p, _StubProvider)
        assert p.close_calls == 1


@pytest.mark.asyncio
async def test_put_rejects_unknown_backend_type(registry: InferenceProviderRegistry) -> None:
    with pytest.raises(UnknownBackendTypeError):
        await registry.put(
            ProviderRecord(
                name="p",
                backend_type="nope",
                credential_name=None,
                default_model="m",
                config={},
            )
        )


@pytest.mark.asyncio
async def test_no_db_pool_returns_empty_list() -> None:
    cm = MagicMock(spec=CredentialManager)
    r = InferenceProviderRegistry(db_pool=None, credential_manager=cm)
    assert await r.list() == []
