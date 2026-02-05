"""Orchestrates provisioning of luthien-proxy instances on Railway."""

from dataclasses import dataclass

from .models import InstanceInfo, InstanceStatus, ProvisioningResult, ServiceInfo, ServiceStatus
from .railway_client import RailwayAPIError, RailwayClient
from .utils import (
    NameValidationError,
    generate_api_key,
    project_name_from_instance,
    validate_instance_name,
)

DEFAULT_REPO = "LuthienResearch/luthien-proxy"


@dataclass
class ProvisioningConfig:
    """Configuration for instance provisioning."""

    repo: str = DEFAULT_REPO


class Provisioner:
    """Orchestrates the creation of luthien-proxy instances."""

    def __init__(self, client: RailwayClient, config: ProvisioningConfig | None = None):
        """Initialize the provisioner with a Railway client and config."""
        self.client = client
        self.config = config or ProvisioningConfig()

    def create_instance(self, name: str) -> ProvisioningResult:
        """Provision a new luthien-proxy instance.

        Creates a Railway project with Postgres, Redis, and gateway services.
        Database templates auto-provision volumes and connection variables.
        """
        try:
            validate_instance_name(name)
        except NameValidationError as e:
            return ProvisioningResult(success=False, error=str(e))

        project_name = project_name_from_instance(name)

        existing = self.client.find_project_by_name(project_name)
        if existing:
            return ProvisioningResult(
                success=False,
                error=f"Instance '{name}' already exists",
            )

        proxy_api_key = generate_api_key()
        admin_api_key = generate_api_key()

        created_project_id = None

        try:
            project = self.client.create_project(
                name=project_name,
                description=f"Luthien Proxy instance: {name}",
            )
            created_project_id = project["id"]

            env_edges = project.get("environments", {}).get("edges", [])
            if not env_edges:
                raise RailwayAPIError("Project created without environments")
            environment_id = env_edges[0]["node"]["id"]

            # Database templates handle volumes + connection vars automatically
            postgres = self.client.create_postgres_service(
                project_id=created_project_id,
                environment_id=environment_id,
            )

            redis = self.client.create_redis_service(
                project_id=created_project_id,
                environment_id=environment_id,
            )

            gateway = self.client.create_gateway_service(
                project_id=created_project_id,
                environment_id=environment_id,
                repo=self.config.repo,
            )
            gateway_id = gateway["id"]

            self.client.set_service_variables_batch(
                project_id=created_project_id,
                service_name="gateway",
                variables={
                    "GATEWAY_PORT": "${{PORT}}",
                    "DATABASE_URL": "${{Postgres.DATABASE_URL}}",
                    "REDIS_URL": "${{Redis.REDIS_URL}}",
                    "PROXY_API_KEY": proxy_api_key,
                    "ADMIN_API_KEY": admin_api_key,
                    "PYTHONUNBUFFERED": "1",
                },
            )

            domain = self.client.generate_service_domain(
                project_id=created_project_id,
                environment_id=environment_id,
                service_id=gateway_id,
            )
            gateway_url = f"https://{domain}"

            # The initial gateway deploy races against Postgres/Redis
            # initialization and fails the healthcheck. Trigger a fresh
            # deploy now that all services and variables are configured.
            self.client.trigger_deployment(gateway_id, environment_id)

            instance = InstanceInfo(
                name=name,
                project_id=created_project_id,
                status=InstanceStatus.PROVISIONING,
                url=gateway_url,
                services={
                    "postgres": ServiceInfo(
                        id=postgres["id"],
                        name="Postgres",
                        status=ServiceStatus.DEPLOYING,
                    ),
                    "redis": ServiceInfo(
                        id=redis["id"],
                        name="Redis",
                        status=ServiceStatus.DEPLOYING,
                    ),
                    "gateway": ServiceInfo(
                        id=gateway_id,
                        name="gateway",
                        status=ServiceStatus.DEPLOYING,
                        url=gateway_url,
                    ),
                },
            )

            return ProvisioningResult(
                success=True,
                instance=instance,
                proxy_api_key=proxy_api_key,
                admin_api_key=admin_api_key,
            )

        except Exception as e:
            if created_project_id:
                try:
                    self.client.delete_project(created_project_id)
                except Exception:
                    pass
            return ProvisioningResult(
                success=False,
                error=f"Provisioning failed: {e}",
            )
