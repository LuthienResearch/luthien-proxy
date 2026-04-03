"""Unit tests for CredentialManager.resolve() method."""

from unittest.mock import AsyncMock

import pytest

from luthien_proxy.credential_manager import CredentialError, CredentialManager
from luthien_proxy.credentials.auth_provider import (
    ServerKey,
    UserCredentials,
    UserThenServer,
)
from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.policy_core.policy_context import PolicyContext


class TestResolveUserCredentials:
    """Test resolve() with UserCredentials auth provider."""

    @pytest.mark.asyncio
    async def test_returns_user_credential_when_set(self):
        """resolve(UserCredentials(), context) returns context.user_credential when set."""
        manager = CredentialManager(db_pool=None, cache=None)
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)
        context = PolicyContext.for_testing(user_credential=cred)

        result = await manager.resolve(UserCredentials(), context)

        assert result == cred

    @pytest.mark.asyncio
    async def test_raises_when_user_credential_missing(self):
        """resolve(UserCredentials(), context) raises CredentialError when user_credential is None."""
        manager = CredentialManager(db_pool=None, cache=None)
        context = PolicyContext.for_testing(user_credential=None)

        with pytest.raises(CredentialError, match="No user credential on request context"):
            await manager.resolve(UserCredentials(), context)


class TestResolveServerKey:
    """Test resolve() with ServerKey auth provider."""

    @pytest.mark.asyncio
    async def test_calls_store_for_server_key(self):
        """resolve(ServerKey("name"), context) calls the internal store."""
        mock_store = AsyncMock()
        cred = Credential(value="sk-server-key", credential_type=CredentialType.API_KEY)
        mock_store.get.return_value = cred

        manager = CredentialManager(db_pool=None, cache=None)
        manager._store = mock_store

        context = PolicyContext.for_testing()
        result = await manager.resolve(ServerKey("test_key"), context)

        mock_store.get.assert_called_once_with("test_key")
        assert result == cred

    @pytest.mark.asyncio
    async def test_raises_when_no_store(self):
        """resolve(ServerKey("name"), context) raises CredentialError when store is None."""
        manager = CredentialManager(db_pool=None, cache=None)
        context = PolicyContext.for_testing()

        with pytest.raises(CredentialError, match="No credential store configured"):
            await manager.resolve(ServerKey("test_key"), context)

    @pytest.mark.asyncio
    async def test_raises_when_key_not_found(self):
        """resolve(ServerKey("name"), context) raises CredentialError when key not found."""
        mock_store = AsyncMock()
        mock_store.get.return_value = None

        manager = CredentialManager(db_pool=None, cache=None)
        manager._store = mock_store

        context = PolicyContext.for_testing()

        with pytest.raises(CredentialError, match="Server key 'missing_key' not found"):
            await manager.resolve(ServerKey("missing_key"), context)


class TestResolveUserThenServer:
    """Test resolve() with UserThenServer auth provider."""

    @pytest.mark.asyncio
    async def test_returns_user_credential_when_available(self):
        """resolve(UserThenServer("name"), context) returns user credential when available."""
        manager = CredentialManager(db_pool=None, cache=None)
        user_cred = Credential(value="sk-ant-user", credential_type=CredentialType.API_KEY)
        context = PolicyContext.for_testing(user_credential=user_cred)

        result = await manager.resolve(UserThenServer("fallback_key"), context)

        assert result == user_cred

    @pytest.mark.asyncio
    async def test_falls_back_with_warn_when_user_missing(self):
        """resolve(UserThenServer("name", on_fallback="warn"), context) falls back to server key with warning log."""
        mock_store = AsyncMock()
        server_cred = Credential(value="sk-server", credential_type=CredentialType.API_KEY)
        mock_store.get.return_value = server_cred

        manager = CredentialManager(db_pool=None, cache=None)
        manager._store = mock_store

        context = PolicyContext.for_testing(user_credential=None)

        result = await manager.resolve(UserThenServer("fallback_key", on_fallback="warn"), context)

        # The actual warning is logged via logger.warning(), which is called
        # internally. We verify the credential is correct and store was called.
        assert result == server_cred
        mock_store.get.assert_called_once_with("fallback_key")

    @pytest.mark.asyncio
    async def test_falls_back_with_fallback_when_user_missing(self):
        """resolve(UserThenServer("name", on_fallback="fallback"), context) silently falls back."""
        mock_store = AsyncMock()
        server_cred = Credential(value="sk-server", credential_type=CredentialType.API_KEY)
        mock_store.get.return_value = server_cred

        manager = CredentialManager(db_pool=None, cache=None)
        manager._store = mock_store

        context = PolicyContext.for_testing(user_credential=None)
        result = await manager.resolve(UserThenServer("fallback_key", on_fallback="fallback"), context)

        assert result == server_cred

    @pytest.mark.asyncio
    async def test_raises_with_fail_when_user_missing(self):
        """resolve(UserThenServer("name", on_fallback="fail"), context) raises CredentialError when user credential is None."""
        manager = CredentialManager(db_pool=None, cache=None)
        context = PolicyContext.for_testing(user_credential=None)

        with pytest.raises(CredentialError, match="No user credential on request context"):
            await manager.resolve(UserThenServer("fallback_key", on_fallback="fail"), context)

    @pytest.mark.asyncio
    async def test_fall_back_to_server_key_when_user_missing_with_fail(self):
        """UserThenServer with on_fallback="fail" raises immediately without trying store."""
        mock_store = AsyncMock()
        manager = CredentialManager(db_pool=None, cache=None)
        manager._store = mock_store

        context = PolicyContext.for_testing(user_credential=None)

        with pytest.raises(CredentialError):
            await manager.resolve(UserThenServer("fallback_key", on_fallback="fail"), context)

        # Store should never be called since we fail before reaching fallback
        mock_store.get.assert_not_called()


class TestResolveUnknownProvider:
    """Test resolve() with unknown provider type."""

    @pytest.mark.asyncio
    async def test_raises_for_unknown_provider_type(self):
        """resolve() raises CredentialError for unknown auth provider type."""
        manager = CredentialManager(db_pool=None, cache=None)
        context = PolicyContext.for_testing()

        # Create a fake provider that doesn't match any known type
        class UnknownProvider:
            pass

        with pytest.raises(CredentialError, match="Unknown auth provider type"):
            await manager.resolve(UnknownProvider(), context)
