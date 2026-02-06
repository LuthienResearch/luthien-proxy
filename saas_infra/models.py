"""Data models for SaaS infrastructure management."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ServiceStatus(Enum):
    """Status of a Railway service."""

    DEPLOYING = "deploying"
    RUNNING = "running"
    FAILED = "failed"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class InstanceStatus(Enum):
    """Overall status of a luthien-proxy instance."""

    PROVISIONING = "provisioning"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    DELETION_SCHEDULED = "deletion_scheduled"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class ServiceInfo:
    """Information about a single Railway service."""

    id: str
    name: str
    status: ServiceStatus
    url: Optional[str] = None


@dataclass
class InstanceInfo:
    """Information about a luthien-proxy instance."""

    name: str
    project_id: str
    status: InstanceStatus
    url: Optional[str] = None
    services: dict[str, ServiceInfo] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    deletion_scheduled_at: Optional[datetime] = None

    @property
    def gateway_url(self) -> Optional[str]:
        """Get the gateway service URL."""
        gateway = self.services.get("gateway")
        return gateway.url if gateway else None


@dataclass
class ProvisioningResult:
    """Result of provisioning a new instance."""

    success: bool
    instance: Optional[InstanceInfo] = None
    error: Optional[str] = None
    proxy_api_key: Optional[str] = None
    admin_api_key: Optional[str] = None

    @property
    def credentials_message(self) -> str:
        """Format credentials for display (only shown once at creation)."""
        if not self.success or not self.proxy_api_key:
            return ""
        return (
            f"\nCredentials (save these - they won't be shown again):\n"
            f"  PROXY_API_KEY: {self.proxy_api_key}\n"
            f"  ADMIN_API_KEY: {self.admin_api_key}"
        )
