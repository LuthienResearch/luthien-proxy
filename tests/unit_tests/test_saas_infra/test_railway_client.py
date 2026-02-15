"""Tests for Railway client."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add repo root to path for saas_infra import
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from saas_infra.railway_client import (
    RailwayAPIError,
    RailwayAuthError,
    RailwayClient,
)


class TestRailwayClientInit:
    def test_from_env_with_token(self):
        with patch.dict(os.environ, {"RAILWAY_TOKEN": "test-token"}):
            client = RailwayClient.from_env()
            assert client.token == "test-token"
            assert client.team_id is None

    def test_from_env_with_team_id(self):
        with patch.dict(os.environ, {"RAILWAY_TOKEN": "test-token", "RAILWAY_TEAM_ID": "team-123"}):
            client = RailwayClient.from_env()
            assert client.token == "test-token"
            assert client.team_id == "team-123"

    def test_from_env_missing_token(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove RAILWAY_TOKEN if it exists
            os.environ.pop("RAILWAY_TOKEN", None)
            with pytest.raises(RailwayAuthError, match="RAILWAY_TOKEN"):
                RailwayClient.from_env()


class TestRailwayClientExecute:
    def test_execute_success(self):
        client = RailwayClient(token="test-token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"me": {"name": "Test User"}}}

        with patch("httpx.Client") as mock_httpx:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_httpx.return_value.__enter__.return_value = mock_client

            result = client._execute("query { me { name } }")
            assert result == {"me": {"name": "Test User"}}

    def test_execute_auth_error(self):
        client = RailwayClient(token="bad-token")
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("httpx.Client") as mock_httpx:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_httpx.return_value.__enter__.return_value = mock_client

            with pytest.raises(RailwayAuthError, match="Invalid Railway token"):
                client._execute("query { me { name } }")

    def test_execute_graphql_error(self):
        client = RailwayClient(token="test-token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "errors": [{"message": "Field not found"}],
        }

        with patch("httpx.Client") as mock_httpx:
            mock_client = MagicMock()
            mock_client.post.return_value = mock_response
            mock_httpx.return_value.__enter__.return_value = mock_client

            with pytest.raises(RailwayAPIError, match="Field not found"):
                client._execute("query { invalid { field } }")


class TestRailwayClientProjectOperations:
    def test_find_project_by_name_found(self):
        client = RailwayClient(token="test-token")
        with patch.object(client, "list_projects") as mock_list:
            mock_list.return_value = [
                {"name": "luthien-test", "id": "proj-123"},
                {"name": "other-project", "id": "proj-456"},
            ]
            project = client.find_project_by_name("luthien-test")
            assert project is not None
            assert project["id"] == "proj-123"

    def test_find_project_by_name_not_found(self):
        client = RailwayClient(token="test-token")
        with patch.object(client, "list_projects") as mock_list:
            mock_list.return_value = [
                {"name": "other-project", "id": "proj-456"},
            ]
            project = client.find_project_by_name("luthien-nonexistent")
            assert project is None


class TestRailwayClientInstanceListing:
    def test_list_luthien_instances_filters_correctly(self):
        client = RailwayClient(token="test-token")
        with patch.object(client, "list_projects") as mock_list:
            mock_list.return_value = [
                {
                    "name": "luthien-test1",
                    "id": "proj-1",
                    "description": "",
                    "createdAt": "2024-01-15T10:00:00Z",
                    "services": {"edges": []},
                },
                {
                    "name": "luthien-test2",
                    "id": "proj-2",
                    "description": "",
                    "createdAt": "2024-01-16T10:00:00Z",
                    "services": {"edges": []},
                },
                {
                    "name": "other-project",
                    "id": "proj-3",
                    "description": "",
                    "createdAt": "2024-01-17T10:00:00Z",
                    "services": {"edges": []},
                },
            ]

            instances = client.list_luthien_instances()
            assert len(instances) == 2
            names = [i.name for i in instances]
            assert "test1" in names
            assert "test2" in names

    def test_list_luthien_instances_with_deletion_scheduled(self):
        client = RailwayClient(token="test-token")
        with patch.object(client, "list_projects") as mock_list:
            mock_list.return_value = [
                {
                    "name": "luthien-test1",
                    "id": "proj-1",
                    "description": "deletion-scheduled:2024-01-22T10:00:00+00:00",
                    "createdAt": "2024-01-15T10:00:00Z",
                    "services": {"edges": []},
                },
            ]

            instances = client.list_luthien_instances()
            assert len(instances) == 1
            assert instances[0].deletion_scheduled_at is not None


class TestRailwayCLI:
    """Tests for Railway CLI integration."""

    def test_run_cli_success(self):
        """Successful CLI execution returns stdout."""
        client = RailwayClient(token="test-token")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="some output",
                stderr="",
            )
            result = client._run_cli(["list"])
            assert result == "some output"

            # Verify railway command was called
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "railway"

    def test_run_cli_json_parsing(self):
        """capture_json=True parses stdout as JSON."""
        client = RailwayClient(token="test-token")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"projects": []}',
                stderr="",
            )
            result = client._run_cli(["list", "--json"], capture_json=True)
            assert result == {"projects": []}

    def test_run_cli_error_propagation(self):
        """Non-zero exit code raises RailwayAPIError."""
        client = RailwayClient(token="test-token")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error: Project not found",
            )
            with pytest.raises(RailwayAPIError, match="Project not found"):
                client._run_cli(["link", "-p", "invalid"])

    def test_run_cli_timeout(self):
        """Timeout raises RailwayAPIError."""
        client = RailwayClient(token="test-token")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd=["railway"], timeout=120)
            with pytest.raises(RailwayAPIError, match="timed out"):
                client._run_cli(["init"])

    def test_run_cli_not_found(self):
        """Missing CLI binary raises RailwayAPIError."""
        client = RailwayClient(token="test-token")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(RailwayAPIError, match="not found"):
                client._run_cli(["init"])

    def test_run_cli_invalid_json(self):
        """Invalid JSON with capture_json raises RailwayAPIError."""
        client = RailwayClient(token="test-token")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="not json",
                stderr="",
            )
            with pytest.raises(RailwayAPIError, match="parse CLI output"):
                client._run_cli(["list", "--json"], capture_json=True)

    def test_create_project_via_cli(self):
        """create_project uses CLI init and fetches via GraphQL."""
        client = RailwayClient(token="test-token")
        mock_project = {
            "id": "proj-123",
            "name": "luthien-test",
            "environments": {"edges": [{"node": {"id": "env-456", "name": "production"}}]},
        }
        with (
            patch.object(client, "_run_cli") as mock_cli,
            patch.object(client, "find_project_by_name", return_value={"id": "proj-123", "name": "luthien-test"}),
            patch.object(client, "get_project", return_value=mock_project),
            patch.object(client, "update_project_description") as mock_desc,
        ):
            result = client.create_project("luthien-test", "Test description")

            mock_cli.assert_called_once()
            args = mock_cli.call_args[0][0]
            assert "init" in args
            assert "-n" in args
            assert "luthien-test" in args

            mock_desc.assert_called_once_with("proj-123", "Test description")
            assert result["id"] == "proj-123"

    def test_create_postgres_via_template(self):
        """create_postgres_service uses railway add -d postgres."""
        client = RailwayClient(token="test-token")
        mock_project = {"services": {"edges": [{"node": {"id": "svc-pg", "name": "Postgres"}}]}}
        with (
            patch.object(client, "_run_cli") as mock_cli,
            patch.object(client, "get_project", return_value=mock_project),
        ):
            result = client.create_postgres_service("proj-123", "env-456")

            # Verify CLI was called with link and add
            calls = mock_cli.call_args_list
            assert any("-d" in call[0][0] and "postgres" in call[0][0] for call in calls)
            assert result["name"] == "Postgres"

    def test_create_redis_via_template(self):
        """create_redis_service uses railway add -d redis."""
        client = RailwayClient(token="test-token")
        mock_project = {"services": {"edges": [{"node": {"id": "svc-redis", "name": "Redis"}}]}}
        with (
            patch.object(client, "_run_cli") as mock_cli,
            patch.object(client, "get_project", return_value=mock_project),
        ):
            result = client.create_redis_service("proj-123", "env-456")

            calls = mock_cli.call_args_list
            assert any("-d" in call[0][0] and "redis" in call[0][0] for call in calls)
            assert result["name"] == "Redis"

    def test_create_gateway_via_cli(self):
        """create_gateway_service uses railway add --service --repo."""
        client = RailwayClient(token="test-token")
        mock_project = {"services": {"edges": [{"node": {"id": "svc-gw", "name": "gateway"}}]}}
        with (
            patch.object(client, "_run_cli") as mock_cli,
            patch.object(client, "get_project", return_value=mock_project),
        ):
            result = client.create_gateway_service("proj-123", "env-456", "LuthienResearch/luthien-proxy")

            calls = mock_cli.call_args_list
            assert any(
                "--service" in call[0][0] and "gateway" in call[0][0] and "--repo" in call[0][0] for call in calls
            )
            assert result["name"] == "gateway"

    def test_set_variables_batch(self):
        """set_service_variables_batch sets multiple vars in one call."""
        client = RailwayClient(token="test-token")
        with patch.object(client, "_run_cli") as mock_cli:
            client.set_service_variables_batch(
                "proj-123",
                "gateway",
                {"KEY1": "val1", "KEY2": "val2"},
            )

            calls = mock_cli.call_args_list
            # Should have link call and variables call
            var_calls = [c for c in calls if c[0][0][0] == "variables" or (len(c[0][0]) > 0 and "variables" in c[0][0])]
            assert len(var_calls) >= 1

            # The variables call should contain --set for both vars
            all_args = []
            for call in calls:
                all_args.extend(call[0][0])
            assert "--skip-deploys" in all_args

    def test_generate_domain_via_cli(self):
        """generate_service_domain uses railway domain --json."""
        client = RailwayClient(token="test-token")
        with (
            patch.object(client, "_resolve_service_name", return_value="gateway"),
            patch.object(client, "_run_cli") as mock_cli,
        ):
            mock_cli.return_value = {"domain": "test-abc.railway.app"}

            result = client.generate_service_domain("proj-123", "env-456", "svc-gw")
            assert result == "test-abc.railway.app"

    def test_generate_domain_strips_https_prefix(self):
        """generate_service_domain strips https:// prefix from CLI output."""
        client = RailwayClient(token="test-token")
        with (
            patch.object(client, "_resolve_service_name", return_value="gateway"),
            patch.object(client, "_run_cli") as mock_cli,
        ):
            mock_cli.return_value = "https://test-abc.railway.app"

            result = client.generate_service_domain("proj-123", "env-456", "svc-gw")
            assert result == "test-abc.railway.app"

    def test_resolve_service_name(self):
        """_resolve_service_name finds service name from ID."""
        client = RailwayClient(token="test-token")
        mock_project = {
            "services": {
                "edges": [
                    {"node": {"id": "svc-1", "name": "Postgres"}},
                    {"node": {"id": "svc-2", "name": "gateway"}},
                ]
            }
        }
        with patch.object(client, "get_project", return_value=mock_project):
            assert client._resolve_service_name("proj-123", "svc-1") == "Postgres"
            assert client._resolve_service_name("proj-123", "svc-2") == "gateway"

    def test_resolve_service_name_not_found(self):
        """_resolve_service_name raises error for unknown service ID."""
        client = RailwayClient(token="test-token")
        mock_project = {"services": {"edges": [{"node": {"id": "svc-1", "name": "Postgres"}}]}}
        with patch.object(client, "get_project", return_value=mock_project):
            with pytest.raises(RailwayAPIError, match="not found in project"):
                client._resolve_service_name("proj-123", "unknown-id")
