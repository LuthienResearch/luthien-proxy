"""Tests for saas-infra data models."""

import sys
from pathlib import Path

# Add repo root to path for saas_infra import
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from saas_infra.models import (
    InstanceInfo,
    InstanceStatus,
    ProvisioningResult,
    ServiceInfo,
    ServiceStatus,
)


class TestServiceInfo:
    def test_basic_service_info(self):
        svc = ServiceInfo(
            id="svc-123",
            name="gateway",
            status=ServiceStatus.RUNNING,
        )
        assert svc.id == "svc-123"
        assert svc.name == "gateway"
        assert svc.status == ServiceStatus.RUNNING
        assert svc.url is None

    def test_service_info_with_url(self):
        svc = ServiceInfo(
            id="svc-123",
            name="gateway",
            status=ServiceStatus.RUNNING,
            url="https://example.railway.app",
        )
        assert svc.url == "https://example.railway.app"


class TestInstanceInfo:
    def test_basic_instance_info(self):
        inst = InstanceInfo(
            name="test-instance",
            project_id="proj-123",
            status=InstanceStatus.RUNNING,
        )
        assert inst.name == "test-instance"
        assert inst.project_id == "proj-123"
        assert inst.status == InstanceStatus.RUNNING
        assert inst.gateway_url is None

    def test_instance_with_services(self):
        gateway = ServiceInfo(
            id="svc-456",
            name="gateway",
            status=ServiceStatus.RUNNING,
            url="https://my-gateway.railway.app",
        )
        inst = InstanceInfo(
            name="test-instance",
            project_id="proj-123",
            status=InstanceStatus.RUNNING,
            services={"gateway": gateway},
        )
        assert inst.gateway_url == "https://my-gateway.railway.app"

    def test_instance_gateway_url_no_gateway(self):
        inst = InstanceInfo(
            name="test-instance",
            project_id="proj-123",
            status=InstanceStatus.RUNNING,
            services={"postgres": ServiceInfo(id="svc-1", name="Postgres", status=ServiceStatus.RUNNING)},
        )
        assert inst.gateway_url is None


class TestProvisioningResult:
    def test_success_result(self):
        inst = InstanceInfo(
            name="test",
            project_id="proj-123",
            status=InstanceStatus.RUNNING,
        )
        result = ProvisioningResult(
            success=True,
            instance=inst,
            proxy_api_key="key123",
            admin_api_key="admin456",
        )
        assert result.success
        assert result.instance == inst
        assert "key123" in result.credentials_message
        assert "admin456" in result.credentials_message

    def test_failure_result(self):
        result = ProvisioningResult(
            success=False,
            error="Something went wrong",
        )
        assert not result.success
        assert result.error == "Something went wrong"
        assert result.credentials_message == ""


class TestServiceStatus:
    def test_all_statuses(self):
        assert ServiceStatus.DEPLOYING.value == "deploying"
        assert ServiceStatus.RUNNING.value == "running"
        assert ServiceStatus.FAILED.value == "failed"
        assert ServiceStatus.STOPPED.value == "stopped"
        assert ServiceStatus.UNKNOWN.value == "unknown"


class TestInstanceStatus:
    def test_all_statuses(self):
        assert InstanceStatus.PROVISIONING.value == "provisioning"
        assert InstanceStatus.RUNNING.value == "running"
        assert InstanceStatus.UNHEALTHY.value == "unhealthy"
        assert InstanceStatus.DELETION_SCHEDULED.value == "deletion_scheduled"
        assert InstanceStatus.FAILED.value == "failed"
        assert InstanceStatus.UNKNOWN.value == "unknown"
