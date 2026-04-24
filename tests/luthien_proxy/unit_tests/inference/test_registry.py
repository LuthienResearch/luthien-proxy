"""Unit tests for `InferenceProviderRegistry`.

Covers dispatch, record-caching + concurrency, credential rotation,
and DB round-trip against a real in-memory SQLite with the #014
migration applied (so we're not asserting against a hand-rolled schema
stub).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.credential_manager import CredentialManager
from luthien_proxy.credentials.credential import Credential, CredentialError, CredentialType
from luthien_proxy.inference.base import InferenceProvider, InferenceResult
from luthien_proxy.inference.registry import (
    MAX_CONFIG_JSON_BYTES,
    InferenceProviderRegistry,
    InferenceRegistryError,
    MissingCredentialError,
    NullCredentialDirectApiProvider,
    NullCredentialError,
    ProviderNotFoundError,
    ProviderRecord,
    UnknownBackendTypeError,
    _build_direct_api,
)
from luthien_proxy.utils.db import DatabasePool


class _StubProvider(InferenceProvider):
    """Minimal provider impl for isolating registry behavior."""

    backend_type = "stub"

    def __init__(self, *, name: str, record: ProviderRecord, credential: Credential | None) -> None:
        super().__init__(name=name)
        self.record = record
        self.credential = credential

    async def complete(self, *args, **kwargs) -> InferenceResult:  # noqa: ANN003
        return InferenceResult.from_text("stub")


class _CountingFactory:
    """Factory that counts invocations; used for concurrency assertions."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, record: ProviderRecord, credential: object) -> InferenceProvider:
        self.calls += 1
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
    """Credential manager stub — only `resolve_server_credential` is used."""
    cm = MagicMock(spec=CredentialManager)
    cm.resolve_server_credential = AsyncMock()
    return cm


@pytest.fixture
async def registry(sqlite_pool: DatabasePool, mock_credential_manager: MagicMock) -> InferenceProviderRegistry:
    """Registry with a counting stub factory so we don't construct real providers."""
    factory = _CountingFactory()
    r = InferenceProviderRegistry(
        db_pool=sqlite_pool,
        credential_manager=mock_credential_manager,
        factories={"stub": factory},
    )
    await r.initialize()
    return r


# --- Basic CRUD round-trip ---


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
        ProviderRecord(name="p1", backend_type="stub", credential_name=None, default_model="m1", config={})
    )
    await registry.put(
        ProviderRecord(name="p1", backend_type="stub", credential_name=None, default_model="m2", config={"k": "v"})
    )
    listed = await registry.list()
    assert len(listed) == 1
    assert listed[0].default_model == "m2"
    assert listed[0].config == {"k": "v"}


@pytest.mark.asyncio
async def test_delete_returns_true_when_row_existed(registry: InferenceProviderRegistry) -> None:
    await registry.put(
        ProviderRecord(name="p1", backend_type="stub", credential_name=None, default_model="m", config={})
    )
    assert await registry.delete("p1") is True
    assert await registry.delete("p1") is False


# --- get() happy + sad paths ---


@pytest.mark.asyncio
async def test_get_raises_when_missing(registry: InferenceProviderRegistry) -> None:
    with pytest.raises(ProviderNotFoundError):
        await registry.get("nope")


@pytest.mark.asyncio
async def test_get_dispatches_on_backend_type(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    cred = Credential(value="x", credential_type=CredentialType.API_KEY)
    mock_credential_manager.resolve_server_credential = AsyncMock(return_value=cred)

    await registry.put(ProviderRecord(name="p", backend_type="stub", credential_name="c", default_model="m", config={}))

    provider = await registry.get("p")
    assert isinstance(provider, _StubProvider)
    assert provider.credential is cred
    mock_credential_manager.resolve_server_credential.assert_awaited_once_with("c")


@pytest.mark.asyncio
async def test_get_raises_for_unknown_backend_type(registry: InferenceProviderRegistry) -> None:
    # Write directly with a bogus backend_type to simulate a row whose
    # backend has been removed since it was created.
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
    mock_credential_manager.resolve_server_credential = AsyncMock(
        side_effect=CredentialError("Server key 'gone' not found")
    )
    await registry.put(
        ProviderRecord(name="p", backend_type="stub", credential_name="gone", default_model="m", config={})
    )
    with pytest.raises(MissingCredentialError) as exc:
        await registry.get("p")
    assert "p" in str(exc.value)
    assert "gone" in str(exc.value)


# --- Concurrency (F) ---


@pytest.mark.asyncio
async def test_concurrent_cold_get_fetches_record_once(
    sqlite_pool: DatabasePool, mock_credential_manager: MagicMock
) -> None:
    """Under `asyncio.gather(get("p"), get("p"))` on a cold cache, the DB
    fetch for the record must run exactly once. The factory runs per
    call (fresh provider per call is the new contract), but the registry
    must not stampede the DB.
    """
    factory = _CountingFactory()

    # Seed a row via a temporary registry so setup reads don't count.
    seed = InferenceProviderRegistry(
        db_pool=sqlite_pool, credential_manager=mock_credential_manager, factories={"stub": factory}
    )
    await seed.put(ProviderRecord(name="p", backend_type="stub", credential_name=None, default_model="m", config={}))

    reg = InferenceProviderRegistry(
        db_pool=sqlite_pool, credential_manager=mock_credential_manager, factories={"stub": factory}
    )

    db_reads = 0
    original_get_record = reg.get_record

    async def counting_get_record(name: str):
        nonlocal db_reads
        db_reads += 1
        return await original_get_record(name)

    reg.get_record = counting_get_record  # type: ignore[method-assign]

    # Cold cache: 8 concurrent gets for the same name.
    results = await asyncio.gather(*(reg.get("p") for _ in range(8)))
    assert len(results) == 8
    assert all(isinstance(r, _StubProvider) for r in results)
    assert db_reads == 1, f"expected exactly one DB read on cold cache, got {db_reads}"


@pytest.mark.asyncio
async def test_concurrent_cold_get_returns_independent_providers(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    """Per-call construction means concurrent gets see distinct instances."""
    mock_credential_manager.resolve_server_credential = AsyncMock(return_value=None)
    await registry.put(
        ProviderRecord(name="p", backend_type="stub", credential_name=None, default_model="m", config={})
    )
    a, b = await asyncio.gather(registry.get("p"), registry.get("p"))
    assert a is not b


# --- Credential rotation (I) ---


@pytest.mark.asyncio
async def test_credential_rotation_reflects_on_next_get(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    """Rotating the underlying credential must take effect on the very next
    `get()` — the registry does not cache provider instances and resolves
    credentials fresh per call.
    """
    first = Credential(value="v1", credential_type=CredentialType.API_KEY)
    second = Credential(value="v2", credential_type=CredentialType.API_KEY)
    mock_credential_manager.resolve_server_credential = AsyncMock(side_effect=[first, second, second])

    await registry.put(ProviderRecord(name="p", backend_type="stub", credential_name="c", default_model="m", config={}))

    p1 = await registry.get("p")
    assert isinstance(p1, _StubProvider)
    assert p1.credential is first

    # Simulate operator rotation without touching the registry at all.
    p2 = await registry.get("p")
    assert isinstance(p2, _StubProvider)
    assert p2.credential is second
    assert mock_credential_manager.resolve_server_credential.await_count == 2


# --- Null-credential DirectApi (G) ---


@pytest.mark.asyncio
async def test_null_credential_direct_api_provider_raises_without_override() -> None:
    """Factory-built null-credential DirectApi provider refuses to
    `complete()` without a `credential_override` and raises a clear
    `NullCredentialError` instead of shipping an empty Bearer header.
    """
    record = ProviderRecord(
        name="passthrough-only",
        backend_type="direct_api",
        credential_name=None,
        default_model="claude-sonnet-4-6",
        config={},
    )
    provider = _build_direct_api(record, None)
    assert isinstance(provider, NullCredentialDirectApiProvider)

    with pytest.raises(NullCredentialError):
        await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            credential_override=None,
        )


# --- put() cache invalidation ---


@pytest.mark.asyncio
async def test_put_invalidates_record_cache(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    mock_credential_manager.resolve_server_credential = AsyncMock(return_value=None)
    await registry.put(
        ProviderRecord(name="p", backend_type="stub", credential_name=None, default_model="m1", config={})
    )
    first = await registry.get("p")
    assert isinstance(first, _StubProvider)
    assert first.record.default_model == "m1"

    await registry.put(
        ProviderRecord(name="p", backend_type="stub", credential_name=None, default_model="m2", config={})
    )
    second = await registry.get("p")
    assert isinstance(second, _StubProvider)
    assert second.record.default_model == "m2"


@pytest.mark.asyncio
async def test_delete_invalidates_record_cache(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    mock_credential_manager.resolve_server_credential = AsyncMock(return_value=None)
    await registry.put(
        ProviderRecord(name="p", backend_type="stub", credential_name=None, default_model="m", config={})
    )
    await registry.get("p")
    await registry.delete("p")
    with pytest.raises(ProviderNotFoundError):
        await registry.get("p")


# --- Config ceiling (M) ---


@pytest.mark.asyncio
async def test_put_rejects_oversized_config(registry: InferenceProviderRegistry) -> None:
    # 65 KiB of filler — 1 KiB over the ceiling.
    huge = {"x": "a" * (MAX_CONFIG_JSON_BYTES + 1024)}
    with pytest.raises(InferenceRegistryError):
        await registry.put(
            ProviderRecord(name="p", backend_type="stub", credential_name=None, default_model="m", config=huge)
        )


# --- Misc ---


@pytest.mark.asyncio
async def test_put_rejects_unknown_backend_type(registry: InferenceProviderRegistry) -> None:
    with pytest.raises(UnknownBackendTypeError):
        await registry.put(
            ProviderRecord(name="p", backend_type="nope", credential_name=None, default_model="m", config={})
        )


@pytest.mark.asyncio
async def test_known_backend_types_reports_factories(registry: InferenceProviderRegistry) -> None:
    assert registry.known_backend_types() == ("stub",)


@pytest.mark.asyncio
async def test_no_db_pool_returns_empty_list() -> None:
    cm = MagicMock(spec=CredentialManager)
    r = InferenceProviderRegistry(db_pool=None, credential_manager=cm)
    assert await r.list() == []


@pytest.mark.asyncio
async def test_close_is_noop_but_clears_cache(
    registry: InferenceProviderRegistry, mock_credential_manager: MagicMock
) -> None:
    mock_credential_manager.resolve_server_credential = AsyncMock(return_value=None)
    await registry.put(
        ProviderRecord(name="p", backend_type="stub", credential_name=None, default_model="m", config={})
    )
    await registry.get("p")
    assert registry._record_cache  # type: ignore[attr-defined]
    await registry.close()
    assert not registry._record_cache  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_config_roundtrip_preserves_nested_structure(
    registry: InferenceProviderRegistry,
) -> None:
    nested = {"api_base": "https://x", "extras": {"a": 1, "b": [1, 2, 3]}}
    await registry.put(
        ProviderRecord(name="p", backend_type="stub", credential_name=None, default_model="m", config=nested)
    )
    listed = await registry.list()
    assert listed[0].config == nested
    assert json.dumps(listed[0].config, sort_keys=True) == json.dumps(nested, sort_keys=True)
