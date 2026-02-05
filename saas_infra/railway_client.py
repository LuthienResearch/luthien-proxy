"""Railway GraphQL API client."""

import json
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .models import InstanceInfo, InstanceStatus, ServiceInfo, ServiceStatus
from .utils import (
    instance_name_from_project,
    parse_deletion_tag,
    project_name_from_instance,
)

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"
DELETION_TAG_KEY = "deletion-scheduled"


class RailwayAPIError(Exception):
    """Raised when Railway API returns an error."""

    pass


class RailwayAuthError(RailwayAPIError):
    """Raised when Railway authentication fails."""

    pass


@dataclass
class RailwayClient:
    """Client for Railway GraphQL API."""

    token: str
    team_id: str | None = None

    @classmethod
    def from_env(cls) -> "RailwayClient":
        """Create client from environment variables."""
        token = os.environ.get("RAILWAY_TOKEN")
        if not token:
            raise RailwayAuthError(
                "RAILWAY_TOKEN environment variable is required. Get one from https://railway.app/account/tokens"
            )
        team_id = os.environ.get("RAILWAY_TEAM_ID")
        return cls(token=token, team_id=team_id)

    def _execute(self, query: str, variables: dict[str, Any] | None = None) -> dict:
        """Execute a GraphQL query."""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        with httpx.Client(timeout=30.0) as client:
            response = client.post(RAILWAY_API_URL, headers=headers, json=payload)

        if response.status_code == 401:
            raise RailwayAuthError("Invalid Railway token")

        # Try to parse JSON first - GraphQL often returns errors with 200 or 400 status
        try:
            data = response.json()
        except Exception:
            # If JSON parsing fails, raise the HTTP error
            response.raise_for_status()
            raise RailwayAPIError(f"Invalid response: {response.text[:200]}")

        # Check for GraphQL errors in response body
        if "errors" in data:
            error_messages = [e.get("message", str(e)) for e in data["errors"]]
            raise RailwayAPIError(f"GraphQL errors: {'; '.join(error_messages)}")

        # Raise HTTP errors for non-200 responses that don't have GraphQL errors
        if response.status_code >= 400:
            response.raise_for_status()

        return data.get("data", {})

    def _run_cli(
        self,
        args: list[str],
        cwd: str | None = None,
        capture_json: bool = False,
    ) -> dict[str, Any] | str:
        """Execute a Railway CLI command.

        Args:
            args: CLI arguments (e.g., ["init", "-n", "my-project"])
            cwd: Working directory (use tmpdir for project-linked commands)
            capture_json: Parse stdout as JSON dict

        Returns:
            Parsed JSON dict if capture_json=True, otherwise stdout string
        """
        # Strip RAILWAY_TOKEN from env so CLI uses its own session auth
        # from ~/.railway/config.json (the dashboard team token format
        # is not accepted by the CLI)
        env = os.environ.copy()
        env.pop("RAILWAY_TOKEN", None)

        cmd = ["railway"] + args

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise RailwayAPIError(f"Railway CLI timed out: {' '.join(cmd)}")
        except FileNotFoundError:
            raise RailwayAPIError("Railway CLI not found. Install with: brew install railway")

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            raise RailwayAPIError(f"Railway CLI error: {error_msg}")

        if capture_json:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as e:
                raise RailwayAPIError(f"Failed to parse CLI output as JSON: {e}")

        return result.stdout.strip()

    @contextmanager
    def _linked_project_dir(self, project_id: str):
        """Create a temp directory linked to a Railway project.

        Yields the tmpdir path. All CLI commands using this cwd
        will operate on the linked project.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_cli(["link", "-p", project_id], cwd=tmpdir)
            yield tmpdir

    def _resolve_service_name(self, project_id: str, service_id: str) -> str:
        """Look up a service's name from its ID."""
        project = self.get_project(project_id)
        if not project:
            raise RailwayAPIError(f"Project {project_id} not found")

        for edge in project.get("services", {}).get("edges", []):
            if edge["node"]["id"] == service_id:
                return edge["node"]["name"]

        raise RailwayAPIError(f"Service {service_id} not found in project {project_id}")

    def _wait_for_service(self, project_id: str, service_name: str, timeout: int = 30) -> dict:
        """Poll until a service appears in the project's service list.

        Railway has a propagation delay between CLI operations
        completing and services being visible via GraphQL.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            project = self.get_project(project_id)
            if project:
                for edge in project.get("services", {}).get("edges", []):
                    if edge["node"]["name"] == service_name:
                        return edge["node"]
            time.sleep(2)

        raise RailwayAPIError(f"Service '{service_name}' not found in project after {timeout}s")

    def get_current_user(self) -> dict:
        """Get current user info to verify authentication."""
        query = """
        query {
            me {
                id
                name
                email
                teams {
                    edges {
                        node {
                            id
                            name
                        }
                    }
                }
            }
        }
        """
        return self._execute(query)["me"]

    def list_projects(self) -> list[dict]:
        """List all projects accessible to the authenticated user."""
        query = """
        query($teamId: String) {
            projects(teamId: $teamId) {
                edges {
                    node {
                        id
                        name
                        description
                        createdAt
                        environments {
                            edges {
                                node {
                                    id
                                    name
                                }
                            }
                        }
                        services {
                            edges {
                                node {
                                    id
                                    name
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        result = self._execute(query, {"teamId": self.team_id})
        return [edge["node"] for edge in result["projects"]["edges"]]

    def get_project(self, project_id: str) -> dict | None:
        """Get detailed project information."""
        query = """
        query($projectId: String!) {
            project(id: $projectId) {
                id
                name
                description
                createdAt
                environments {
                    edges {
                        node {
                            id
                            name
                        }
                    }
                }
                services {
                    edges {
                        node {
                            id
                            name
                            deployments {
                                edges {
                                    node {
                                        id
                                        status
                                        createdAt
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        result = self._execute(query, {"projectId": project_id})
        return result.get("project")

    def create_project(self, name: str, description: str = "") -> dict:
        """Create a new Railway project using CLI."""
        with tempfile.TemporaryDirectory() as tmpdir:
            init_args = ["init", "-n", name]
            self._run_cli(init_args, cwd=tmpdir)

        project = self.find_project_by_name(name)
        if not project:
            raise RailwayAPIError(f"Project '{name}' created but not found via API")

        full_project = self.get_project(project["id"])
        if not full_project:
            raise RailwayAPIError(f"Project '{name}' created but details not available")

        if description:
            self.update_project_description(full_project["id"], description)

        return full_project

    def delete_project(self, project_id: str) -> bool:
        """Delete a Railway project."""
        query = """
        mutation($projectId: String!) {
            projectDelete(id: $projectId)
        }
        """
        self._execute(query, {"projectId": project_id})
        return True

    def update_project_description(self, project_id: str, description: str) -> bool:
        """Update project description (used for soft-delete tagging)."""
        query = """
        mutation($id: String!, $input: ProjectUpdateInput!) {
            projectUpdate(id: $id, input: $input) {
                id
            }
        }
        """
        self._execute(query, {"id": project_id, "input": {"description": description}})
        return True

    def create_postgres_service(self, project_id: str, environment_id: str) -> dict:
        """Create a PostgreSQL service using Railway's official template.

        The template auto-provisions volumes and connection variables.
        """
        with self._linked_project_dir(project_id) as tmpdir:
            self._run_cli(["add", "-d", "postgres"], cwd=tmpdir)

        return self._wait_for_service(project_id, "Postgres")

    def create_redis_service(self, project_id: str, environment_id: str) -> dict:
        """Create a Redis service using Railway's official template.

        The template auto-provisions volumes and connection variables.
        """
        with self._linked_project_dir(project_id) as tmpdir:
            self._run_cli(["add", "-d", "redis"], cwd=tmpdir)

        return self._wait_for_service(project_id, "Redis")

    def create_gateway_service(self, project_id: str, environment_id: str, repo: str) -> dict:
        """Create the gateway service from a GitHub repo.

        Args:
            project_id: Railway project ID
            environment_id: Railway environment ID
            repo: GitHub repo in "owner/repo" format
        """
        with self._linked_project_dir(project_id) as tmpdir:
            self._run_cli(
                ["add", "--service", "gateway", "--repo", repo],
                cwd=tmpdir,
            )

        return self._wait_for_service(project_id, "gateway")

    def upsert_variable(
        self,
        project_id: str,
        environment_id: str,
        service_id: str,
        name: str,
        value: str,
    ) -> bool:
        """Create or update an environment variable using CLI."""
        service_name = self._resolve_service_name(project_id, service_id)

        with self._linked_project_dir(project_id) as tmpdir:
            self._run_cli(
                ["variables", "--service", service_name, "--set", f"{name}={value}", "--skip-deploys"],
                cwd=tmpdir,
            )
        return True

    def set_service_variables_batch(
        self,
        project_id: str,
        service_name: str,
        variables: dict[str, str],
    ) -> bool:
        """Set multiple variables for a service in a single CLI call."""
        set_args: list[str] = []
        for var_name, var_value in variables.items():
            set_args.extend(["--set", f"{var_name}={var_value}"])

        with self._linked_project_dir(project_id) as tmpdir:
            self._run_cli(
                ["variables", "--service", service_name, "--skip-deploys", *set_args],
                cwd=tmpdir,
            )
        return True

    def upsert_shared_variable(
        self,
        project_id: str,
        environment_id: str,
        name: str,
        value: str,
    ) -> bool:
        """Create or update a shared environment variable using CLI."""
        with self._linked_project_dir(project_id) as tmpdir:
            self._run_cli(
                ["variables", "--set", f"{name}={value}", "--skip-deploys"],
                cwd=tmpdir,
            )
        return True

    def trigger_deployment(self, service_id: str, environment_id: str) -> bool:
        """Trigger a new deployment for a service."""
        query = """
        mutation($environmentId: String!, $serviceId: String!) {
            serviceInstanceRedeploy(environmentId: $environmentId, serviceId: $serviceId)
        }
        """
        self._execute(
            query,
            {"environmentId": environment_id, "serviceId": service_id},
        )
        return True

    def get_service_domains(self, project_id: str, environment_id: str, service_id: str) -> list[str]:
        """Get domains for a service."""
        query = """
        query($projectId: String!, $environmentId: String!, $serviceId: String!) {
            domains(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId) {
                serviceDomains {
                    domain
                }
                customDomains {
                    domain
                }
            }
        }
        """
        result = self._execute(
            query, {"projectId": project_id, "environmentId": environment_id, "serviceId": service_id}
        )
        domains = result.get("domains", {})
        service_domains = [d["domain"] for d in domains.get("serviceDomains", [])]
        custom_domains = [d["domain"] for d in domains.get("customDomains", [])]
        return service_domains + custom_domains

    def generate_service_domain(self, project_id: str, environment_id: str, service_id: str) -> str:
        """Generate a Railway domain for a service using CLI."""
        service_name = self._resolve_service_name(project_id, service_id)

        with self._linked_project_dir(project_id) as tmpdir:
            result = self._run_cli(
                ["domain", "--service", service_name, "--json"],
                cwd=tmpdir,
                capture_json=True,
            )

        domain = None
        if isinstance(result, dict) and "domain" in result:
            domain = result["domain"]
        elif isinstance(result, str) and result:
            domain = result

        if not domain:
            raise RailwayAPIError(f"No domain in CLI response: {result}")

        # CLI sometimes returns full URL; callers expect bare domain
        return domain.removeprefix("https://").removeprefix("http://").strip()

    def find_project_by_name(self, name: str) -> dict | None:
        """Find a project by exact name match."""
        projects = self.list_projects()
        for project in projects:
            if project["name"] == name:
                return project
        return None

    def list_luthien_instances(self) -> list[InstanceInfo]:
        """List all luthien-proxy instances."""
        projects = self.list_projects()
        instances = []

        for project in projects:
            instance_name = instance_name_from_project(project["name"])
            if instance_name is None:
                continue

            deletion_scheduled = None
            status = InstanceStatus.UNKNOWN
            description = project.get("description", "")

            if description and description.startswith(f"{DELETION_TAG_KEY}:"):
                tag_value = description[len(DELETION_TAG_KEY) + 1 :].strip()
                deletion_scheduled = parse_deletion_tag(tag_value)
                if deletion_scheduled:
                    status = InstanceStatus.DELETION_SCHEDULED
                else:
                    status = InstanceStatus.RUNNING
            else:
                status = InstanceStatus.RUNNING

            services = {}
            for edge in project.get("services", {}).get("edges", []):
                svc = edge["node"]
                services[svc["name"].lower()] = ServiceInfo(
                    id=svc["id"],
                    name=svc["name"],
                    status=ServiceStatus.UNKNOWN,
                )

            created_at = None
            if project.get("createdAt"):
                try:
                    created_at = datetime.fromisoformat(project["createdAt"].replace("Z", "+00:00"))
                except ValueError:
                    pass

            instances.append(
                InstanceInfo(
                    name=instance_name,
                    project_id=project["id"],
                    status=status,
                    services=services,
                    created_at=created_at,
                    deletion_scheduled_at=deletion_scheduled,
                )
            )

        return instances

    def get_instance(self, instance_name: str) -> InstanceInfo | None:
        """Get detailed info for a specific instance."""
        project_name = project_name_from_instance(instance_name)
        project = self.find_project_by_name(project_name)
        if not project:
            return None

        deletion_scheduled = None
        status = InstanceStatus.RUNNING
        description = project.get("description", "")

        if description and description.startswith(f"{DELETION_TAG_KEY}:"):
            tag_value = description[len(DELETION_TAG_KEY) + 1 :].strip()
            deletion_scheduled = parse_deletion_tag(tag_value)
            if deletion_scheduled:
                status = InstanceStatus.DELETION_SCHEDULED

        # Get environment ID for domain queries
        env_edges = project.get("environments", {}).get("edges", [])
        environment_id = env_edges[0]["node"]["id"] if env_edges else None

        services = {}
        gateway_url = None

        for edge in project.get("services", {}).get("edges", []):
            svc = edge["node"]
            svc_name = svc["name"].lower()

            svc_status = ServiceStatus.UNKNOWN
            deployments = svc.get("deployments", {}).get("edges", [])
            if deployments:
                latest = deployments[0]["node"]
                deploy_status = latest.get("status", "").upper()
                if deploy_status == "SUCCESS":
                    svc_status = ServiceStatus.RUNNING
                elif deploy_status in ("BUILDING", "DEPLOYING"):
                    svc_status = ServiceStatus.DEPLOYING
                elif deploy_status in ("FAILED", "CRASHED"):
                    svc_status = ServiceStatus.FAILED

            svc_url = None
            if svc_name == "gateway" and environment_id:
                try:
                    domains = self.get_service_domains(project["id"], environment_id, svc["id"])
                    if domains:
                        svc_url = f"https://{domains[0]}"
                        gateway_url = svc_url
                except RailwayAPIError:
                    pass

            services[svc_name] = ServiceInfo(
                id=svc["id"],
                name=svc["name"],
                status=svc_status,
                url=svc_url,
            )

        created_at = None
        if project.get("createdAt"):
            try:
                created_at = datetime.fromisoformat(project["createdAt"].replace("Z", "+00:00"))
            except ValueError:
                pass

        return InstanceInfo(
            name=instance_name,
            project_id=project["id"],
            status=status,
            url=gateway_url,
            services=services,
            created_at=created_at,
            deletion_scheduled_at=deletion_scheduled,
        )

    def schedule_deletion(self, instance_name: str, deletion_date: datetime) -> bool:
        """Mark an instance for deletion by updating project description."""
        project_name = project_name_from_instance(instance_name)
        project = self.find_project_by_name(project_name)
        if not project:
            raise RailwayAPIError(f"Instance '{instance_name}' not found")

        description = f"{DELETION_TAG_KEY}:{deletion_date.isoformat()}"
        return self.update_project_description(project["id"], description)

    def cancel_deletion(self, instance_name: str) -> bool:
        """Cancel scheduled deletion by clearing the description."""
        project_name = project_name_from_instance(instance_name)
        project = self.find_project_by_name(project_name)
        if not project:
            raise RailwayAPIError(f"Instance '{instance_name}' not found")

        return self.update_project_description(project["id"], "")

    def force_delete_instance(self, instance_name: str) -> bool:
        """Immediately delete an instance (no grace period)."""
        project_name = project_name_from_instance(instance_name)
        project = self.find_project_by_name(project_name)
        if not project:
            raise RailwayAPIError(f"Instance '{instance_name}' not found")

        return self.delete_project(project["id"])
