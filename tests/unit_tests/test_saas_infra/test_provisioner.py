"""Tests for the Provisioner orchestrator."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from saas_infra.models import InstanceStatus
from saas_infra.provisioner import Provisioner, ProvisioningConfig
from saas_infra.railway_client import RailwayAPIError, RailwayClient


def _make_mock_client():
    """Create a mock RailwayClient with standard responses."""
    client = MagicMock(spec=RailwayClient)
    client.find_project_by_name.return_value = None  # No existing project

    client.create_project.return_value = {
        "id": "proj-123",
        "name": "luthien-test",
        "environments": {"edges": [{"node": {"id": "env-456", "name": "production"}}]},
    }

    client.create_postgres_service.return_value = {"id": "svc-pg", "name": "Postgres"}
    client.create_redis_service.return_value = {"id": "svc-redis", "name": "Redis"}
    client.create_gateway_service.return_value = {"id": "svc-gw", "name": "gateway"}

    client.set_service_variables_batch.return_value = True
    client.generate_service_domain.return_value = "luthien-test-prod.railway.app"

    return client


class TestProvisionerCreateInstance:
    """Tests for Provisioner.create_instance()."""

    def test_success(self):
        """Full provisioning flow succeeds."""
        client = _make_mock_client()
        provisioner = Provisioner(client)

        result = provisioner.create_instance("test")

        assert result.success is True
        assert result.instance is not None
        assert result.instance.name == "test"
        assert result.instance.url == "https://luthien-test-prod.railway.app"
        assert result.instance.status == InstanceStatus.PROVISIONING
        assert result.proxy_api_key is not None
        assert result.admin_api_key is not None

        # Verify services were created
        assert "postgres" in result.instance.services
        assert "redis" in result.instance.services
        assert "gateway" in result.instance.services

        # Verify gateway variables were set in batch
        client.set_service_variables_batch.assert_called_once()
        call_kwargs = client.set_service_variables_batch.call_args[1]
        assert call_kwargs["service_name"] == "gateway"
        variables = call_kwargs["variables"]
        assert "DATABASE_URL" in variables
        assert "REDIS_URL" in variables
        assert "PROXY_API_KEY" in variables
        assert "ADMIN_API_KEY" in variables

    def test_invalid_name(self):
        """Invalid instance name fails before API calls."""
        client = _make_mock_client()
        provisioner = Provisioner(client)

        result = provisioner.create_instance("INVALID")

        assert result.success is False
        assert "lowercase" in result.error.lower() or "alphanumeric" in result.error.lower()

        # No API calls should have been made
        client.create_project.assert_not_called()

    def test_duplicate_name(self):
        """Existing project with same name fails."""
        client = _make_mock_client()
        client.find_project_by_name.return_value = {"id": "existing", "name": "luthien-test"}
        provisioner = Provisioner(client)

        result = provisioner.create_instance("test")

        assert result.success is False
        assert "already exists" in result.error

        client.create_project.assert_not_called()

    def test_cleanup_on_failure(self):
        """Project is cleaned up when provisioning fails partway through."""
        client = _make_mock_client()
        client.create_redis_service.side_effect = RailwayAPIError("Redis creation failed")
        provisioner = Provisioner(client)

        result = provisioner.create_instance("test")

        assert result.success is False
        assert "Redis creation failed" in result.error

        # Project should have been cleaned up
        client.delete_project.assert_called_once_with("proj-123")

    def test_cleanup_failure_does_not_mask_original_error(self):
        """If cleanup also fails, original error is still reported."""
        client = _make_mock_client()
        client.create_gateway_service.side_effect = RailwayAPIError("Gateway failed")
        client.delete_project.side_effect = RailwayAPIError("Delete also failed")
        provisioner = Provisioner(client)

        result = provisioner.create_instance("test")

        assert result.success is False
        assert "Gateway failed" in result.error

    def test_no_environments_in_project(self):
        """Fails gracefully when project has no environments."""
        client = _make_mock_client()
        client.create_project.return_value = {
            "id": "proj-123",
            "name": "luthien-test",
            "environments": {"edges": []},
        }
        provisioner = Provisioner(client)

        result = provisioner.create_instance("test")

        assert result.success is False
        assert "without environments" in result.error

    def test_custom_repo(self):
        """Custom repo is passed to gateway service creation."""
        client = _make_mock_client()
        config = ProvisioningConfig(repo="myorg/my-fork")
        provisioner = Provisioner(client, config)

        result = provisioner.create_instance("test")

        assert result.success is True
        client.create_gateway_service.assert_called_once()
        call_kwargs = client.create_gateway_service.call_args[1]
        assert call_kwargs["repo"] == "myorg/my-fork"

    def test_unique_api_keys(self):
        """Each instance gets unique API keys."""
        client = _make_mock_client()
        provisioner = Provisioner(client)

        result = provisioner.create_instance("test")

        assert result.proxy_api_key != result.admin_api_key
        assert len(result.proxy_api_key) == 32
        assert len(result.admin_api_key) == 32
