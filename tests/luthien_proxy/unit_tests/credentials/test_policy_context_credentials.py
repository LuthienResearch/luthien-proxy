"""Unit tests for PolicyContext credential integration."""

import copy

import pytest

from luthien_proxy.credential_manager import CredentialManager
from luthien_proxy.credentials.credential import Credential, CredentialError, CredentialType
from luthien_proxy.policy_core.policy_context import PolicyContext


class TestPolicyContextUserCredential:
    """Test PolicyContext.user_credential property."""

    def test_user_credential_is_accessible(self):
        """user_credential property is set and accessible."""
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)
        context = PolicyContext.for_testing(user_credential=cred)

        assert context.user_credential == cred

    def test_user_credential_defaults_to_none(self):
        """user_credential defaults to None."""
        context = PolicyContext.for_testing()

        assert context.user_credential is None


class TestPolicyContextCredentialManager:
    """Test PolicyContext.credential_manager property."""

    def test_credential_manager_returns_manager_when_set(self):
        """credential_manager property returns manager when set."""
        manager = CredentialManager(db_pool=None, cache=None)
        context = PolicyContext.for_testing(credential_manager=manager)

        assert context.credential_manager is manager

    def test_credential_manager_raises_when_none(self):
        """credential_manager property raises CredentialError when None."""
        context = PolicyContext.for_testing(credential_manager=None)

        with pytest.raises(CredentialError, match="No credential manager configured"):
            _ = context.credential_manager


class TestPolicyContextDeepCopy:
    """Test PolicyContext.__deepcopy__ credential handling."""

    def test_deepcopy_shares_user_credential(self):
        """__deepcopy__ shares user_credential with the copy."""
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)
        context = PolicyContext.for_testing(user_credential=cred)

        context_copy = copy.deepcopy(context)

        # Should reference the same credential object
        assert context_copy.user_credential is cred

    def test_deepcopy_shares_credential_manager(self):
        """__deepcopy__ shares _credential_manager with the copy."""
        manager = CredentialManager(db_pool=None, cache=None)
        context = PolicyContext.for_testing(credential_manager=manager)

        context_copy = copy.deepcopy(context)

        # Should reference the same manager object
        assert context_copy._credential_manager is manager

    def test_deepcopy_preserves_credential_manager_property(self):
        """__deepcopy__ copy can access credential_manager property."""
        manager = CredentialManager(db_pool=None, cache=None)
        context = PolicyContext.for_testing(credential_manager=manager)

        context_copy = copy.deepcopy(context)

        assert context_copy.credential_manager is manager


class TestPolicyContextForTesting:
    """Test PolicyContext.for_testing() constructor."""

    def test_for_testing_accepts_credential_params(self):
        """for_testing() accepts credential parameters."""
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)
        manager = CredentialManager(db_pool=None, cache=None)

        context = PolicyContext.for_testing(
            user_credential=cred,
            credential_manager=manager,
        )

        assert context.user_credential == cred
        assert context.credential_manager is manager

    def test_for_testing_defaults_to_no_credentials(self):
        """for_testing() with no params sets credentials to None."""
        context = PolicyContext.for_testing()

        assert context.user_credential is None
        assert context._credential_manager is None

    def test_for_testing_with_custom_transaction_id(self):
        """for_testing() accepts custom transaction_id."""
        context = PolicyContext.for_testing(transaction_id="custom-txn-123")

        assert context.transaction_id == "custom-txn-123"
