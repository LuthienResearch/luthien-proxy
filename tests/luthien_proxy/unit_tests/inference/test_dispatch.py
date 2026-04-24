"""Tests for `luthien_proxy.inference.dispatch.resolve_inference_provider`.

The dispatcher is the glue between a policy's declared `InferenceProviderRef`
and a concrete `(provider, credential_override)` pair. The tests lock in
the three branches of the reference union and the three on_fallback modes.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.credentials import (
    Credential,
    CredentialError,
    CredentialType,
    Provider,
    UserCredentials,
    UserThenProvider,
)
from luthien_proxy.inference.base import InferenceProvider
from luthien_proxy.inference.dispatch import resolve_inference_provider
from luthien_proxy.policy_core.policy_context import PolicyContext


def _user_cred() -> Credential:
    return Credential(value="sk-ant-USER", credential_type=CredentialType.AUTH_TOKEN)


def _fake_registry_provider() -> InferenceProvider:
    provider = MagicMock(spec=InferenceProvider)
    provider.name = "fake-registry-provider"
    return provider


def _mock_registry(provider: InferenceProvider | None = None) -> MagicMock:
    registry = MagicMock()
    registry.get = AsyncMock(return_value=provider or _fake_registry_provider())
    return registry


class TestUserCredentials:
    """Request has a user credential and the policy wants passthrough."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_passthrough_and_user_cred(self):
        context = PolicyContext.for_testing(user_credential=_user_cred())
        result = await resolve_inference_provider(
            UserCredentials(),
            context,
            registry=None,
            passthrough_default_model="claude-haiku-4-5",
        )
        assert result.credential_override is context.user_credential
        assert result.provider.backend_type == "direct_api"

    @pytest.mark.asyncio
    async def test_missing_user_cred_raises(self):
        context = PolicyContext.for_testing(user_credential=None)
        with pytest.raises(CredentialError, match="No user credential"):
            await resolve_inference_provider(
                UserCredentials(),
                context,
                registry=None,
                passthrough_default_model="claude-haiku-4-5",
            )


class TestProvider:
    """Policy references a named provider in the registry."""

    @pytest.mark.asyncio
    async def test_looks_up_registry_and_returns_no_override(self):
        context = PolicyContext.for_testing(user_credential=None)
        registered = _fake_registry_provider()
        registry = _mock_registry(registered)
        result = await resolve_inference_provider(
            Provider(name="my-judge"),
            context,
            registry=registry,
            passthrough_default_model="claude-haiku-4-5",
        )
        registry.get.assert_awaited_once_with("my-judge")
        assert result.provider is registered
        assert result.credential_override is None

    @pytest.mark.asyncio
    async def test_missing_registry_raises(self):
        context = PolicyContext.for_testing(user_credential=None)
        with pytest.raises(RuntimeError, match="no InferenceProviderRegistry is configured"):
            await resolve_inference_provider(
                Provider(name="my-judge"),
                context,
                registry=None,
                passthrough_default_model="claude-haiku-4-5",
            )


class TestUserThenProvider:
    """Try user cred; fall back per `on_fallback`."""

    @pytest.mark.asyncio
    async def test_user_cred_present_uses_passthrough(self):
        context = PolicyContext.for_testing(user_credential=_user_cred())
        registry = _mock_registry()
        result = await resolve_inference_provider(
            UserThenProvider(name="my-judge", on_fallback="warn"),
            context,
            registry=registry,
            passthrough_default_model="claude-haiku-4-5",
        )
        assert result.credential_override is context.user_credential
        registry.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fail_mode_raises_when_no_user_cred(self):
        context = PolicyContext.for_testing(user_credential=None)
        registry = _mock_registry()
        with pytest.raises(CredentialError, match="on_fallback='fail'"):
            await resolve_inference_provider(
                UserThenProvider(name="my-judge", on_fallback="fail"),
                context,
                registry=registry,
                passthrough_default_model="claude-haiku-4-5",
            )
        registry.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_warn_mode_falls_back_and_logs(self, caplog):
        context = PolicyContext.for_testing(user_credential=None)
        registered = _fake_registry_provider()
        registry = _mock_registry(registered)
        with caplog.at_level(logging.WARNING):
            result = await resolve_inference_provider(
                UserThenProvider(name="my-judge", on_fallback="warn"),
                context,
                registry=registry,
                passthrough_default_model="claude-haiku-4-5",
            )
        assert result.provider is registered
        assert result.credential_override is None
        assert any("my-judge" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_fallback_mode_silent(self, caplog):
        context = PolicyContext.for_testing(user_credential=None)
        registered = _fake_registry_provider()
        registry = _mock_registry(registered)
        with caplog.at_level(logging.WARNING):
            result = await resolve_inference_provider(
                UserThenProvider(name="my-judge", on_fallback="fallback"),
                context,
                registry=registry,
                passthrough_default_model="claude-haiku-4-5",
            )
        assert result.provider is registered
        # No warning log
        assert not any(rec.levelname == "WARNING" for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_fallback_requires_registry_when_no_user_cred(self):
        context = PolicyContext.for_testing(user_credential=None)
        with pytest.raises(RuntimeError, match="no InferenceProviderRegistry is configured"):
            await resolve_inference_provider(
                UserThenProvider(name="my-judge", on_fallback="fallback"),
                context,
                registry=None,
                passthrough_default_model="claude-haiku-4-5",
            )
