"""Unit tests for BasePolicy passthrough auth helpers.

Tests _extract_passthrough_key (credential extraction from request headers)
and _resolve_judge_credential (priority resolution for judge LLM calls).
"""

from __future__ import annotations

from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.types import RawHttpRequest


def make_request(headers: dict[str, str]) -> RawHttpRequest:
    return RawHttpRequest(body={}, headers=headers)


class TestExtractPassthroughKey:
    def test_none_request_returns_none(self) -> None:
        assert BasePolicy._extract_passthrough_key(None) is None

    def test_bearer_token_extracted_as_bearer(self) -> None:
        req = make_request({"authorization": "Bearer sk-ant-abc123"})
        cred = BasePolicy._extract_passthrough_key(req)
        assert cred is not None
        assert cred.value == "sk-ant-abc123"
        assert cred.is_bearer is True

    def test_bearer_case_insensitive(self) -> None:
        req = make_request({"authorization": "BEARER sk-ant-abc123"})
        cred = BasePolicy._extract_passthrough_key(req)
        assert cred is not None
        assert cred.value == "sk-ant-abc123"
        assert cred.is_bearer is True

    def test_x_api_key_extracted_as_non_bearer(self) -> None:
        req = make_request({"x-api-key": "sk-ant-abc123"})
        cred = BasePolicy._extract_passthrough_key(req)
        assert cred is not None
        assert cred.value == "sk-ant-abc123"
        assert cred.is_bearer is False

    def test_bearer_takes_priority_over_x_api_key(self) -> None:
        req = make_request(
            {
                "authorization": "Bearer bearer-key",
                "x-api-key": "x-api-key-value",
            }
        )
        cred = BasePolicy._extract_passthrough_key(req)
        assert cred is not None
        assert cred.value == "bearer-key"
        assert cred.is_bearer is True

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


class TestResolveJudgeCredential:
    """Test _resolve_judge_credential priority and credential type propagation."""

    def _make_context(self, headers: dict[str, str] | None = None) -> PolicyContext:
        ctx = PolicyContext.for_testing(transaction_id="test-txn")
        if headers is not None:
            ctx.raw_http_request = RawHttpRequest(body={}, headers=headers)
        return ctx

    def test_explicit_key_takes_priority(self) -> None:
        """Explicit per-policy key wins over passthrough and fallback."""
        policy = BasePolicy()
        ctx = self._make_context({"authorization": "Bearer oauth-token"})
        cred = policy._resolve_judge_credential(ctx, "explicit-key", "fallback-key")
        assert cred is not None
        assert cred.value == "explicit-key"
        assert cred.is_bearer is False

    def test_passthrough_bearer_preserved(self) -> None:
        """OAuth token from request is returned with is_bearer=True."""
        policy = BasePolicy()
        ctx = self._make_context({"authorization": "Bearer oauth-token"})
        cred = policy._resolve_judge_credential(ctx, None, "fallback-key")
        assert cred is not None
        assert cred.value == "oauth-token"
        assert cred.is_bearer is True

    def test_passthrough_api_key_preserved(self) -> None:
        """x-api-key from request is returned with is_bearer=False."""
        policy = BasePolicy()
        ctx = self._make_context({"x-api-key": "sk-ant-abc123"})
        cred = policy._resolve_judge_credential(ctx, None, "fallback-key")
        assert cred is not None
        assert cred.value == "sk-ant-abc123"
        assert cred.is_bearer is False

    def test_fallback_key_used_when_no_passthrough(self) -> None:
        """Server fallback key used when no passthrough credential."""
        policy = BasePolicy()
        ctx = self._make_context({})
        cred = policy._resolve_judge_credential(ctx, None, "fallback-key")
        assert cred is not None
        assert cred.value == "fallback-key"
        assert cred.is_bearer is False

    def test_returns_none_when_nothing_available(self) -> None:
        """Returns None when no key is available from any source."""
        policy = BasePolicy()
        ctx = self._make_context({})
        cred = policy._resolve_judge_credential(ctx, None, None)
        assert cred is None

    def test_no_raw_request_falls_to_fallback(self) -> None:
        """When raw_http_request is None, falls through to fallback."""
        policy = BasePolicy()
        ctx = PolicyContext.for_testing(transaction_id="test-txn")
        # raw_http_request defaults to None
        cred = policy._resolve_judge_credential(ctx, None, "fallback-key")
        assert cred is not None
        assert cred.value == "fallback-key"
        assert cred.is_bearer is False
