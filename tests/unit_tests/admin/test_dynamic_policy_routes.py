"""Unit tests for dynamic policy API routes."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from luthien_proxy.admin.dynamic_policy_routes import router
from luthien_proxy.dependencies import get_db_pool, get_policy_manager

# Valid policy source for testing
VALID_POLICY_SOURCE = """
from luthien_proxy.policy_core.base_policy import BasePolicy
class TestPolicy(BasePolicy):
    @property
    def short_policy_name(self):
        return "Test"
"""


@pytest.fixture
def app() -> FastAPI:
    """Create a test FastAPI app with the dynamic policy router."""
    app = FastAPI()
    app.include_router(router)

    # Mock admin token verification
    from luthien_proxy.auth import verify_admin_token

    app.dependency_overrides[verify_admin_token] = lambda: "test-admin"

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestValidateEndpoint:
    """Tests for POST /admin/policies/validate."""

    def test_valid_code(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/policies/validate",
            json={"source_code": VALID_POLICY_SOURCE, "config": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["class_name"] == "TestPolicy"

    def test_invalid_syntax(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/policies/validate",
            json={"source_code": "def broken(:", "config": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert len(data["issues"]) > 0

    def test_disallowed_import(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/policies/validate",
            json={"source_code": "import os\nclass Foo:\n  pass", "config": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert any("os" in i for i in data["issues"])


class TestGenerateEndpoint:
    """Tests for POST /admin/policies/generate."""

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False)
    def test_generate_without_api_key(self, client: TestClient) -> None:
        # Remove the key temporarily
        import os

        old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            resp = client.post(
                "/admin/policies/generate",
                json={"prompt": "a no-op policy"},
            )
            assert resp.status_code == 503
        finally:
            if old_key:
                os.environ["ANTHROPIC_API_KEY"] = old_key

    @patch("luthien_proxy.admin.dynamic_policy_routes.generate_policy_code")
    def test_generate_success(self, mock_generate: AsyncMock, client: TestClient) -> None:
        mock_generate.return_value = {"code": VALID_POLICY_SOURCE, "model": "test-model"}

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            resp = client.post(
                "/admin/policies/generate",
                json={"prompt": "a no-op policy"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == VALID_POLICY_SOURCE
        assert data["model"] == "test-model"


class TestListEndpoint:
    """Tests for GET /admin/policies/."""

    def test_list_without_db(self, client: TestClient, app: FastAPI) -> None:
        from luthien_proxy.dependencies import get_db_pool

        app.dependency_overrides[get_db_pool] = lambda: None

        resp = client.get("/admin/policies/")
        assert resp.status_code == 503

    def test_list_with_db(self, client: TestClient, app: FastAPI) -> None:
        mock_pool = MagicMock()
        mock_db_pool = MagicMock()
        mock_db_pool.get_pool = AsyncMock(return_value=mock_pool)
        mock_pool.fetch = AsyncMock(return_value=[])

        from luthien_proxy.dependencies import get_db_pool

        app.dependency_overrides[get_db_pool] = lambda: mock_db_pool

        resp = client.get("/admin/policies/")
        assert resp.status_code == 200
        assert resp.json() == []


def _make_db_fixtures(app: FastAPI):
    """Set up mock DB pool and policy manager on the app, return (mock_pool, mock_manager)."""
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()
    mock_conn.transaction = MagicMock(return_value=AsyncContextManagerMock())

    mock_db_pool = MagicMock()
    mock_db_pool.get_pool = AsyncMock(return_value=mock_pool)
    mock_db_pool.connection = MagicMock(return_value=AsyncContextManagerMockYielding(mock_conn))

    mock_manager = MagicMock()
    mock_manager.set_dynamic_policy = MagicMock()

    app.dependency_overrides[get_db_pool] = lambda: mock_db_pool
    app.dependency_overrides[get_policy_manager] = lambda: mock_manager

    return mock_pool, mock_manager


class TestGetPolicyEndpoint:
    """Tests for GET /admin/policies/{policy_id}."""

    def test_get_policy_not_found(self, client: TestClient, app: FastAPI) -> None:
        mock_pool, _ = _make_db_fixtures(app)
        mock_pool.fetchrow = AsyncMock(return_value=None)

        resp = client.get(f"/admin/policies/{uuid4()}")
        assert resp.status_code == 404

    def test_get_policy_found(self, client: TestClient, app: FastAPI) -> None:
        mock_pool, _ = _make_db_fixtures(app)
        policy_id = uuid4()
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "id": policy_id,
                "name": "TestPolicy",
                "description": "A test policy",
                "source_code": VALID_POLICY_SOURCE,
                "config": json.dumps({}),
                "prompt": "make a test policy",
                "is_active": False,
                "version": 1,
                "created_at": datetime(2026, 1, 1),
                "updated_at": datetime(2026, 1, 1),
                "created_by": "admin",
            }
        )

        resp = client.get(f"/admin/policies/{policy_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "TestPolicy"
        assert data["source_code"] == VALID_POLICY_SOURCE


class TestActivatePolicyEndpoint:
    """Tests for POST /admin/policies/{policy_id}/activate."""

    def test_activate_not_found(self, client: TestClient, app: FastAPI) -> None:
        mock_pool, _ = _make_db_fixtures(app)
        mock_pool.fetchrow = AsyncMock(return_value=None)

        resp = client.post(f"/admin/policies/{uuid4()}/activate")
        assert resp.status_code == 404

    def test_activate_success(self, client: TestClient, app: FastAPI) -> None:
        mock_pool, mock_manager = _make_db_fixtures(app)
        policy_id = uuid4()
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "source_code": VALID_POLICY_SOURCE,
                "config": json.dumps({}),
                "name": "TestPolicy",
            }
        )
        mock_pool.execute = AsyncMock()

        resp = client.post(f"/admin/policies/{policy_id}/activate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mock_manager.set_dynamic_policy.assert_called_once()

    def test_activate_invalid_source_fails(self, client: TestClient, app: FastAPI) -> None:
        mock_pool, _ = _make_db_fixtures(app)
        mock_pool.fetchrow = AsyncMock(
            return_value={
                "source_code": "import os\nclass Foo:\n  pass",
                "config": json.dumps({}),
                "name": "BadPolicy",
            }
        )

        resp = client.post(f"/admin/policies/{uuid4()}/activate")
        assert resp.status_code == 400


class TestDeletePolicyEndpoint:
    """Tests for DELETE /admin/policies/{policy_id}."""

    def test_delete_not_found(self, client: TestClient, app: FastAPI) -> None:
        mock_pool, _ = _make_db_fixtures(app)
        mock_pool.fetchrow = AsyncMock(return_value=None)

        resp = client.delete(f"/admin/policies/{uuid4()}")
        assert resp.status_code == 404

    def test_delete_active_policy_rejected(self, client: TestClient, app: FastAPI) -> None:
        mock_pool, _ = _make_db_fixtures(app)
        mock_pool.fetchrow = AsyncMock(return_value={"is_active": True, "name": "Active"})

        resp = client.delete(f"/admin/policies/{uuid4()}")
        assert resp.status_code == 400
        assert "active" in resp.json()["detail"].lower()

    def test_delete_inactive_policy_succeeds(self, client: TestClient, app: FastAPI) -> None:
        mock_pool, _ = _make_db_fixtures(app)
        mock_pool.fetchrow = AsyncMock(return_value={"is_active": False, "name": "Inactive"})
        mock_pool.execute = AsyncMock()

        resp = client.delete(f"/admin/policies/{uuid4()}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True


class AsyncContextManagerMock:
    """Mock for async context managers like conn.transaction()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class AsyncContextManagerMockYielding:
    """Mock for async context managers that yield a value (like db_pool.connection())."""

    def __init__(self, value: object):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        pass
