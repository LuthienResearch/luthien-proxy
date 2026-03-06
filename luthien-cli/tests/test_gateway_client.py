"""Tests for gateway client."""

import httpx
import pytest
from luthien_cli.gateway_client import GatewayClient, GatewayError


@pytest.fixture
def client():
    return GatewayClient(
        base_url="http://localhost:8000",
        api_key="sk-test",
        admin_key="admin-test",
    )


def test_health_success(client, httpx_mock):
    httpx_mock.add_response(
        url="http://localhost:8000/health",
        json={"status": "healthy", "version": "2.0.0"},
    )
    result = client.health()
    assert result["status"] == "healthy"


def test_health_connection_error(client, httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(GatewayError, match="Cannot connect"):
        client.health()


def test_get_current_policy(client, httpx_mock):
    httpx_mock.add_response(
        url="http://localhost:8000/api/admin/policy/current",
        json={
            "policy": "NoOpPolicy",
            "class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "enabled_at": "2026-03-03T10:00:00",
            "enabled_by": "api",
            "config": {},
        },
    )
    result = client.get_current_policy()
    assert result["policy"] == "NoOpPolicy"


def test_get_auth_config(client, httpx_mock):
    httpx_mock.add_response(
        url="http://localhost:8000/api/admin/auth/config",
        json={
            "auth_mode": "both",
            "validate_credentials": True,
            "valid_cache_ttl_seconds": 300,
            "invalid_cache_ttl_seconds": 60,
        },
    )
    result = client.get_auth_config()
    assert result["auth_mode"] == "both"
