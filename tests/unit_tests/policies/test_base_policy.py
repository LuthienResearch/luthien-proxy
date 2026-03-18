"""Unit tests for BasePolicy._extract_passthrough_key."""

from __future__ import annotations

from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.types import RawHttpRequest


def make_request(headers: dict[str, str]) -> RawHttpRequest:
    return RawHttpRequest(body={}, headers=headers)


class TestExtractPassthroughKey:
    def test_none_request_returns_none(self) -> None:
        assert BasePolicy._extract_passthrough_key(None) is None

    def test_bearer_token_extracted(self) -> None:
        req = make_request({"authorization": "Bearer sk-ant-abc123"})
        assert BasePolicy._extract_passthrough_key(req) == "sk-ant-abc123"

    def test_bearer_case_insensitive(self) -> None:
        req = make_request({"authorization": "BEARER sk-ant-abc123"})
        assert BasePolicy._extract_passthrough_key(req) == "sk-ant-abc123"

    def test_x_api_key_extracted(self) -> None:
        req = make_request({"x-api-key": "sk-ant-abc123"})
        assert BasePolicy._extract_passthrough_key(req) == "sk-ant-abc123"

    def test_bearer_takes_priority_over_x_api_key(self) -> None:
        req = make_request(
            {
                "authorization": "Bearer bearer-key",
                "x-api-key": "x-api-key-value",
            }
        )
        assert BasePolicy._extract_passthrough_key(req) == "bearer-key"

    def test_empty_bearer_token_returns_none(self) -> None:
        req = make_request({"authorization": "Bearer "})
        assert BasePolicy._extract_passthrough_key(req) is None

    def test_no_auth_headers_returns_none(self) -> None:
        req = make_request({"content-type": "application/json"})
        assert BasePolicy._extract_passthrough_key(req) is None

    def test_empty_x_api_key_returns_none(self) -> None:
        req = make_request({"x-api-key": ""})
        assert BasePolicy._extract_passthrough_key(req) is None

    def test_non_bearer_authorization_ignored(self) -> None:
        req = make_request({"authorization": "Basic dXNlcjpwYXNz"})
        assert BasePolicy._extract_passthrough_key(req) is None
