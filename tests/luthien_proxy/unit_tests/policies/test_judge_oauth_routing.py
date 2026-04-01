"""Tests that judge policy call sites route OAuth tokens via extra_headers.

Verifies that ToolCallJudgePolicy and SimpleLLMPolicy pass OAuth bearer
tokens through the Authorization header (via extra_headers) rather than
via x-api-key (via api_key), while plain API keys still go through api_key.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from luthien_proxy.policies.simple_llm_policy import SimpleLLMPolicy
from luthien_proxy.policies.simple_llm_utils import (
    BlockDescriptor,
    JudgeAction,
    SimpleLLMJudgeConfig,
)
from luthien_proxy.policies.tool_call_judge_policy import (
    ToolCallJudgeConfig,
    ToolCallJudgePolicy,
)
from luthien_proxy.policies.tool_call_judge_utils import JudgeResult
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.types import RawHttpRequest

# ============================================================================
# Helpers
# ============================================================================


def _make_context(headers: dict[str, str] | None = None) -> PolicyContext:
    ctx = PolicyContext.for_testing(transaction_id="test-txn")
    if headers is not None:
        ctx.raw_http_request = RawHttpRequest(body={}, headers=headers)
    return ctx


def _make_tool_judge(**overrides: Any) -> ToolCallJudgePolicy:
    config = ToolCallJudgeConfig(**overrides)
    return ToolCallJudgePolicy(config)


def _make_simple_llm(**overrides: Any) -> SimpleLLMPolicy:
    defaults: dict[str, Any] = {"instructions": "test instructions", "on_error": "block"}
    defaults.update(overrides)
    config = SimpleLLMJudgeConfig(**defaults)
    return SimpleLLMPolicy(config)


ALLOWED = JudgeResult(probability=0.0, explanation="safe", prompt=[], response_text="{}")
PASS_ACTION = JudgeAction(action="pass")


# ============================================================================
# ToolCallJudgePolicy
# ============================================================================


class TestToolCallJudgeOAuthRouting:
    """ToolCallJudgePolicy routes OAuth tokens via extra_headers."""

    @pytest.mark.asyncio
    async def test_bearer_token_sent_via_extra_headers(self) -> None:
        """OAuth token from Bearer header goes via extra_headers, not api_key."""
        policy = _make_tool_judge()
        ctx = _make_context({"authorization": "Bearer oauth-token-123"})

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = ALLOWED
            await policy._evaluate_and_maybe_block_anthropic(
                {"id": "t1", "name": "bash", "arguments": "{}"},
                ctx,
            )

            mock_judge.assert_called_once()
            _, kwargs = mock_judge.call_args
            assert kwargs["api_key"] is None
            assert kwargs["extra_headers"] == {"authorization": "Bearer oauth-token-123"}

    @pytest.mark.asyncio
    async def test_api_key_sent_via_api_key_param(self) -> None:
        """Plain API key from x-api-key header goes via api_key param."""
        policy = _make_tool_judge()
        ctx = _make_context({"x-api-key": "sk-ant-abc123"})

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = ALLOWED
            await policy._evaluate_and_maybe_block_anthropic(
                {"id": "t1", "name": "bash", "arguments": "{}"},
                ctx,
            )

            mock_judge.assert_called_once()
            _, kwargs = mock_judge.call_args
            assert kwargs["api_key"] == "sk-ant-abc123"
            assert kwargs["extra_headers"] is None

    @pytest.mark.asyncio
    async def test_fallback_key_sent_via_api_key_param(self) -> None:
        """Server fallback key goes via api_key param (not bearer)."""
        policy = _make_tool_judge()
        # Force a known fallback key
        policy._fallback_api_key = "server-fallback-key"
        ctx = _make_context({})

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = ALLOWED
            await policy._evaluate_and_maybe_block_anthropic(
                {"id": "t1", "name": "bash", "arguments": "{}"},
                ctx,
            )

            mock_judge.assert_called_once()
            _, kwargs = mock_judge.call_args
            assert kwargs["api_key"] == "server-fallback-key"
            assert kwargs["extra_headers"] is None

    @pytest.mark.asyncio
    async def test_no_credential_passes_none(self) -> None:
        """When no credential is available, api_key=None and no extra_headers."""
        policy = _make_tool_judge()
        policy._fallback_api_key = None
        ctx = _make_context({})

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = ALLOWED
            await policy._evaluate_and_maybe_block_anthropic(
                {"id": "t1", "name": "bash", "arguments": "{}"},
                ctx,
            )

            mock_judge.assert_called_once()
            _, kwargs = mock_judge.call_args
            assert kwargs["api_key"] is None
            assert kwargs["extra_headers"] is None


# ============================================================================
# SimpleLLMPolicy
# ============================================================================


class TestSimpleLLMOAuthRouting:
    """SimpleLLMPolicy routes OAuth tokens via extra_headers."""

    @pytest.mark.asyncio
    async def test_bearer_token_sent_via_extra_headers(self) -> None:
        """OAuth token from Bearer header goes via extra_headers, not api_key."""
        policy = _make_simple_llm()
        ctx = _make_context({"authorization": "Bearer oauth-token-456"})

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.return_value = PASS_ACTION
            descriptor = BlockDescriptor(type="text", content="hello")
            await policy._judge_block(descriptor, [], ctx)

            mock_judge.assert_called_once()
            _, kwargs = mock_judge.call_args
            assert kwargs["api_key"] is None
            assert kwargs["extra_headers"] == {"authorization": "Bearer oauth-token-456"}

    @pytest.mark.asyncio
    async def test_api_key_sent_via_api_key_param(self) -> None:
        """Plain API key from x-api-key header goes via api_key param."""
        policy = _make_simple_llm()
        ctx = _make_context({"x-api-key": "sk-ant-abc123"})

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.return_value = PASS_ACTION
            descriptor = BlockDescriptor(type="text", content="hello")
            await policy._judge_block(descriptor, [], ctx)

            mock_judge.assert_called_once()
            _, kwargs = mock_judge.call_args
            assert kwargs["api_key"] == "sk-ant-abc123"
            assert kwargs["extra_headers"] is None

    @pytest.mark.asyncio
    async def test_fallback_key_sent_via_api_key_param(self) -> None:
        """Server fallback key goes via api_key param."""
        policy = _make_simple_llm()
        policy._fallback_api_key = "server-fallback-key"
        ctx = _make_context({})

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.return_value = PASS_ACTION
            descriptor = BlockDescriptor(type="text", content="hello")
            await policy._judge_block(descriptor, [], ctx)

            mock_judge.assert_called_once()
            _, kwargs = mock_judge.call_args
            assert kwargs["api_key"] == "server-fallback-key"
            assert kwargs["extra_headers"] is None

    @pytest.mark.asyncio
    async def test_no_credential_passes_none(self) -> None:
        """When no credential is available, api_key=None and no extra_headers."""
        policy = _make_simple_llm()
        policy._fallback_api_key = None
        ctx = _make_context({})

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge", new_callable=AsyncMock
        ) as mock_judge:
            mock_judge.return_value = PASS_ACTION
            descriptor = BlockDescriptor(type="text", content="hello")
            await policy._judge_block(descriptor, [], ctx)

            mock_judge.assert_called_once()
            _, kwargs = mock_judge.call_args
            assert kwargs["api_key"] is None
            assert kwargs["extra_headers"] is None
