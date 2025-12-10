# ABOUTME: Unit tests for admin route handlers
# ABOUTME: Tests HTTP layer for policy management endpoints

"""Tests for admin route handlers.

These tests focus on the HTTP layer - ensuring routes properly:
- Handle dependency injection
- Convert service exceptions to appropriate HTTP status codes
- Return correct response models
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from luthien_proxy.admin.routes import (
    PolicyEnableResponse,
    PolicySetRequest,
    set_policy,
)
from luthien_proxy.policy_manager import PolicyEnableResult

AUTH_TOKEN = "test-admin-key"


class TestSetPolicyRoute:
    """Test set_policy route handler."""

    @pytest.mark.asyncio
    async def test_successful_set_policy(self):
        """Test successful policy set returns success response."""
        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(
            return_value=PolicyEnableResult(
                success=True,
                policy="luthien_proxy.policies.noop_policy:NoOpPolicy",
                restart_duration_ms=50,
            )
        )

        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
            enabled_by="test",
        )

        result = await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert isinstance(result, PolicyEnableResponse)
        assert result.success is True
        assert result.policy == "luthien_proxy.policies.noop_policy:NoOpPolicy"
        assert result.restart_duration_ms == 50

        mock_manager.enable_policy.assert_called_once_with(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
            enabled_by="test",
        )

    @pytest.mark.asyncio
    async def test_set_policy_with_config(self):
        """Test policy set with configuration parameters."""
        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(
            return_value=PolicyEnableResult(
                success=True,
                policy="luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
                restart_duration_ms=100,
            )
        )

        config = {"judge_model": "claude-haiku-4-5", "block_threshold": 0.8}
        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
            config=config,
            enabled_by="e2e-test",
        )

        result = await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert result.success is True
        mock_manager.enable_policy.assert_called_once_with(
            policy_class_ref="luthien_proxy.policies.simple_judge_policy:SimpleJudgePolicy",
            config=config,
            enabled_by="e2e-test",
        )

    @pytest.mark.asyncio
    async def test_set_policy_failure(self):
        """Test policy set failure returns error response."""
        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(
            return_value=PolicyEnableResult(
                success=False,
                error="Module not found: nonexistent.policy",
                troubleshooting=["Check that the policy class reference is correct"],
            )
        )

        request = PolicySetRequest(
            policy_class_ref="nonexistent.policy:BadPolicy",
            config={},
        )

        result = await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert isinstance(result, PolicyEnableResponse)
        assert result.success is False
        assert "Module not found" in (result.error or "")
        assert result.troubleshooting is not None
        assert len(result.troubleshooting) > 0

    @pytest.mark.asyncio
    async def test_set_policy_http_exception_passthrough(self):
        """Test that HTTPExceptions from manager are passed through."""
        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(
            side_effect=HTTPException(status_code=403, detail="Policy changes disabled")
        )

        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
        )

        with pytest.raises(HTTPException) as exc_info:
            await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert exc_info.value.status_code == 403
        assert "Policy changes disabled" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_set_policy_unexpected_exception(self):
        """Test that unexpected exceptions become 500 errors."""
        mock_manager = MagicMock()
        mock_manager.enable_policy = AsyncMock(side_effect=RuntimeError("Unexpected database error"))

        request = PolicySetRequest(
            policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
            config={},
        )

        with pytest.raises(HTTPException) as exc_info:
            await set_policy(body=request, _=AUTH_TOKEN, manager=mock_manager)

        assert exc_info.value.status_code == 500
        assert "Unexpected database error" in exc_info.value.detail
