"""Unit tests for BasePolicy passthrough auth helpers: _extract_passthrough_key and _judge_oauth_headers."""

from __future__ import annotations

from luthien_proxy.policies import PolicyContext
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


class TestJudgeOAuthHeaders:
    OAUTH_TOKEN = "claude-oauth-token-not-an-api-key"
    API_KEY = "sk-ant-api03-abc123"
    OAUTH_HEADER = {"anthropic-beta": "oauth-2025-04-20"}

    def _ctx(self, headers: dict[str, str]) -> PolicyContext:
        return PolicyContext.for_testing(raw_http_request=RawHttpRequest(body={}, headers=headers))

    def test_oauth_bearer_returns_header(self) -> None:
        ctx = self._ctx({"authorization": f"Bearer {self.OAUTH_TOKEN}"})
        assert BasePolicy._judge_oauth_headers(ctx, None) == self.OAUTH_HEADER

    def test_anthropic_api_key_returns_none(self) -> None:
        ctx = self._ctx({"authorization": f"Bearer {self.API_KEY}"})
        assert BasePolicy._judge_oauth_headers(ctx, None) is None

    def test_explicit_key_skips_oauth_check(self) -> None:
        # Even with an OAuth bearer, an explicit override key suppresses the header
        ctx = self._ctx({"authorization": f"Bearer {self.OAUTH_TOKEN}"})
        assert BasePolicy._judge_oauth_headers(ctx, "explicit-key") is None

    def test_no_request_returns_none(self) -> None:
        from luthien_proxy.policies import PolicyContext

        ctx = PolicyContext.for_testing(raw_http_request=None)
        assert BasePolicy._judge_oauth_headers(ctx, None) is None

    def test_x_api_key_header_not_treated_as_oauth(self) -> None:
        # x-api-key is never OAuth — only Bearer tokens can be OAuth
        ctx = self._ctx({"x-api-key": self.OAUTH_TOKEN})
        assert BasePolicy._judge_oauth_headers(ctx, None) is None
