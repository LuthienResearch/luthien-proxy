"""HTTP client for luthien-proxy gateway APIs."""

from __future__ import annotations

from typing import Any

import httpx


class GatewayError(Exception):
    """Error communicating with gateway."""


class GatewayClient:
    """Thin HTTP client for gateway admin/health APIs."""

    def __init__(self, base_url: str, api_key: str | None = None, admin_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.admin_key = admin_key

    def _admin_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.admin_key:
            headers["Authorization"] = f"Bearer {self.admin_key}"
        return headers

    def _get(self, path: str, admin: bool = False) -> dict[str, Any]:
        headers = self._admin_headers() if admin else {}
        try:
            response = httpx.get(f"{self.base_url}{path}", headers=headers, timeout=10.0)
        except httpx.ConnectError:
            raise GatewayError(f"Cannot connect to gateway at {self.base_url}")
        except httpx.TimeoutException:
            raise GatewayError(f"Gateway at {self.base_url} timed out")

        if response.status_code == 401:
            raise GatewayError("Authentication failed — check your admin_key")
        if response.status_code == 403:
            raise GatewayError("Forbidden — admin access required")
        response.raise_for_status()
        return response.json()

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def get_current_policy(self) -> dict[str, Any]:
        return self._get("/api/admin/policy/current", admin=True)

    def get_auth_config(self) -> dict[str, Any]:
        return self._get("/api/admin/auth/config", admin=True)
