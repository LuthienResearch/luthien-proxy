"""JSON API contract snapshot tests for 4 endpoints.

These tests capture the response shape (keys + types, not values) and fail if the shape changes.
Snapshots are stored in tests/luthien_proxy/perf_tests/snapshots/ and can be regenerated with --update-snapshots.

Marked with @pytest.mark.perf and @pytest.mark.contract for selective execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from luthien_proxy.perf.seeding import seed_sessions


@pytest.fixture(scope="session")
def seeded_perf_db(perf_db_url: str) -> None:
    """Seed the perf DB with test data once per session."""
    import sqlite3
    from pathlib import Path

    db_path = Path.home() / ".luthien" / "perf.db"
    conn = sqlite3.connect(str(db_path))
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM conversation_calls").fetchone()
        if count == 0:
            seed_sessions("sqlite", tier=100)
    finally:
        conn.close()


def _extract_shape(obj: Any) -> Any:
    """Extract type structure from a value (not the value itself).

    Examples:
    - 123 → "int"
    - "hello" → "str"
    - True → "bool"
    - None → "null"
    - [1, 2] → ["int"] (first element's type)
    - {"a": 1} → {"a": "int"}

    Note: for lists, only the first element is inspected. Heterogeneous lists
    (e.g. discriminated union event payloads) will not have shape drift detected
    past index 0. This is acceptable for API contract testing — the snapshot
    captures the common element shape, not exhaustive coverage of every variant.
    """
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "bool"
    if isinstance(obj, int):
        return "int"
    if isinstance(obj, float):
        return "float"
    if isinstance(obj, str):
        return "str"
    if isinstance(obj, list):
        if not obj:
            return "list[unknown]"
        return [_extract_shape(obj[0])]
    if isinstance(obj, dict):
        return {k: _extract_shape(v) for k, v in obj.items()}
    return type(obj).__name__


def _get_snapshots_dir() -> Path:
    """Get the snapshots directory, creating it if needed."""
    snapshots_dir = Path(__file__).parent / "snapshots"
    snapshots_dir.mkdir(exist_ok=True)
    return snapshots_dir


def _load_snapshot(name: str) -> dict[str, Any]:
    """Load a snapshot from disk."""
    snapshot_file = _get_snapshots_dir() / f"{name}.json"
    if not snapshot_file.exists():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_file}")
    with open(snapshot_file) as f:
        return json.load(f)


def _save_snapshot(name: str, data: dict[str, Any]) -> None:
    """Save a snapshot to disk."""
    snapshot_file = _get_snapshots_dir() / f"{name}.json"
    with open(snapshot_file, "w") as f:
        json.dump(data, f, indent=2)


@pytest.fixture
def update_snapshots(request: pytest.FixtureRequest) -> bool:
    """Check if --update-snapshots flag was passed."""
    return request.config.getoption("--update-snapshots", default=False)


@pytest.mark.perf
@pytest.mark.contract
async def test_policy_current_contract(
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    perf_db_url: str,
    update_snapshots: bool,
    seeded_perf_db: None,
) -> None:
    """Test /api/admin/policy/current response shape."""
    async with httpx.AsyncClient(base_url=perf_gateway_url) as client:
        response = await client.get(
            "/api/admin/policy/current",
            headers=admin_headers,
        )

    assert response.status_code == 200
    data = response.json()
    shape = _extract_shape(data)

    if update_snapshots:
        _save_snapshot("policy_current", shape)
    else:
        expected = _load_snapshot("policy_current")
        assert shape == expected, (
            f"Shape mismatch:\nGot: {json.dumps(shape, indent=2)}\nExpected: {json.dumps(expected, indent=2)}"
        )


@pytest.mark.perf
@pytest.mark.contract
@pytest.mark.timeout(30)
async def test_session_detail_contract(
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    perf_db_url: str,
    update_snapshots: bool,
    seeded_perf_db: None,
) -> None:
    """Test /api/history/sessions/{session_id} response shape."""
    async with httpx.AsyncClient(base_url=perf_gateway_url) as client:
        list_response = await client.get(
            "/api/history/sessions?limit=1",
            headers=admin_headers,
        )

    assert list_response.status_code == 200
    sessions = list_response.json()["sessions"]
    assert len(sessions) > 0, "No sessions found in database"
    session_id = sessions[0]["session_id"]

    async with httpx.AsyncClient(base_url=perf_gateway_url) as client:
        response = await client.get(
            f"/api/history/sessions/{session_id}",
            headers=admin_headers,
        )

    assert response.status_code == 200
    data = response.json()
    shape = _extract_shape(data)

    if update_snapshots:
        _save_snapshot("session_detail", shape)
    else:
        expected = _load_snapshot("session_detail")
        assert shape == expected, (
            f"Shape mismatch:\nGot: {json.dumps(shape, indent=2)}\nExpected: {json.dumps(expected, indent=2)}"
        )


@pytest.mark.perf
@pytest.mark.contract
async def test_calls_list_contract(
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    perf_db_url: str,
    update_snapshots: bool,
    seeded_perf_db: None,
) -> None:
    """Test /api/debug/calls response shape."""
    async with httpx.AsyncClient(base_url=perf_gateway_url) as client:
        response = await client.get(
            "/api/debug/calls?limit=20",
            headers=admin_headers,
        )

    assert response.status_code == 200
    data = response.json()
    shape = _extract_shape(data)

    if update_snapshots:
        _save_snapshot("calls_list", shape)
    else:
        expected = _load_snapshot("calls_list")
        assert shape == expected, (
            f"Shape mismatch:\nGot: {json.dumps(shape, indent=2)}\nExpected: {json.dumps(expected, indent=2)}"
        )


@pytest.mark.perf
@pytest.mark.contract
async def test_sessions_list_contract(
    perf_gateway_url: str,
    admin_headers: dict[str, str],
    perf_db_url: str,
    update_snapshots: bool,
    seeded_perf_db: None,
) -> None:
    """Test /api/history/sessions response shape."""
    async with httpx.AsyncClient(base_url=perf_gateway_url) as client:
        response = await client.get(
            "/api/history/sessions?limit=20",
            headers=admin_headers,
        )

    assert response.status_code == 200
    data = response.json()
    shape = _extract_shape(data)

    if update_snapshots:
        _save_snapshot("sessions_list", shape)
    else:
        expected = _load_snapshot("sessions_list")
        assert shape == expected, (
            f"Shape mismatch:\nGot: {json.dumps(shape, indent=2)}\nExpected: {json.dumps(expected, indent=2)}"
        )
