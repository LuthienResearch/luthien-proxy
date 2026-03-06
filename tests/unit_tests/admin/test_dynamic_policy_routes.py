"""Unit tests for dynamic policy API routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from luthien_proxy.admin.dynamic_policy_routes import router

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
    """Tests for POST /api/admin/policies/validate."""

    def test_valid_code(self, client: TestClient) -> None:
        resp = client.post(
            "/api/admin/policies/validate",
            json={"source_code": VALID_POLICY_SOURCE, "config": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["class_name"] == "TestPolicy"

    def test_invalid_syntax(self, client: TestClient) -> None:
        resp = client.post(
            "/api/admin/policies/validate",
            json={"source_code": "def broken(:", "config": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert len(data["issues"]) > 0

    def test_disallowed_import(self, client: TestClient) -> None:
        resp = client.post(
            "/api/admin/policies/validate",
            json={"source_code": "import os\nclass Foo:\n  pass", "config": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert any("os" in i for i in data["issues"])


class TestGenerateEndpoint:
    """Tests for POST /api/admin/policies/generate."""

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False)
    def test_generate_without_api_key(self, client: TestClient) -> None:
        # Remove the key temporarily
        import os

        old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            resp = client.post(
                "/api/admin/policies/generate",
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
                "/api/admin/policies/generate",
                json={"prompt": "a no-op policy"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == VALID_POLICY_SOURCE
        assert data["model"] == "test-model"


class TestListEndpoint:
    """Tests for GET /api/admin/policies/."""

    def test_list_without_db(self, client: TestClient, app: FastAPI) -> None:
        from luthien_proxy.dependencies import get_db_pool

        app.dependency_overrides[get_db_pool] = lambda: None

        resp = client.get("/api/admin/policies/")
        assert resp.status_code == 503

    def test_list_with_db(self, client: TestClient, app: FastAPI) -> None:
        mock_pool = MagicMock()
        mock_db_pool = MagicMock()
        mock_db_pool.get_pool = AsyncMock(return_value=mock_pool)
        mock_pool.fetch = AsyncMock(return_value=[])

        from luthien_proxy.dependencies import get_db_pool

        app.dependency_overrides[get_db_pool] = lambda: mock_db_pool

        resp = client.get("/api/admin/policies/")
        assert resp.status_code == 200
        assert resp.json() == []
