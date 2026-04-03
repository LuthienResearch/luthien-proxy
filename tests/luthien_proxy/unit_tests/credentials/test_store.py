"""Unit tests for CredentialStore."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from luthien_proxy.credentials.credential import Credential, CredentialError, CredentialType
from luthien_proxy.credentials.store import CredentialStore
from luthien_proxy.utils.db import DatabasePool


@pytest.fixture
def db_pool():
    """Mock database pool."""
    return MagicMock(spec=DatabasePool)


@pytest.fixture
def mock_asyncpg_pool():
    """Mock asyncpg pool."""
    return AsyncMock()


@pytest.fixture
async def credential_store_no_encryption(db_pool, mock_asyncpg_pool):
    """CredentialStore without encryption."""
    db_pool.get_pool = AsyncMock(return_value=mock_asyncpg_pool)
    return CredentialStore(db_pool)


@pytest.fixture
async def credential_store_with_encryption(db_pool, mock_asyncpg_pool):
    """CredentialStore with encryption enabled."""
    db_pool.get_pool = AsyncMock(return_value=mock_asyncpg_pool)
    key = Fernet.generate_key()
    return CredentialStore(db_pool, encryption_key=key)


@pytest.mark.asyncio
async def test_get_returns_none_when_not_found(credential_store_no_encryption, mock_asyncpg_pool):
    """get() returns None when credential not found."""
    mock_asyncpg_pool.fetchrow = AsyncMock(return_value=None)

    result = await credential_store_no_encryption.get("missing")

    assert result is None
    mock_asyncpg_pool.fetchrow.assert_called_once()


@pytest.mark.asyncio
async def test_get_returns_credential_from_db(credential_store_no_encryption, mock_asyncpg_pool):
    """get() returns Credential object from DB row."""
    mock_row = {
        "credential_value": "test-api-key",
        "credential_type": "api_key",
        "platform": "anthropic",
        "platform_url": "https://api.anthropic.com",
        "is_encrypted": False,
        "expiry": None,
    }
    mock_asyncpg_pool.fetchrow = AsyncMock(return_value=mock_row)

    result = await credential_store_no_encryption.get("test-key")

    assert result is not None
    assert result.value == "test-api-key"
    assert result.credential_type == CredentialType.API_KEY
    assert result.platform == "anthropic"
    assert result.platform_url == "https://api.anthropic.com"
    assert result.expiry is None


@pytest.mark.asyncio
async def test_get_decrypts_when_encrypted_and_key_set(db_pool, mock_asyncpg_pool):
    """get() decrypts credential value when is_encrypted=True and key is set."""
    key = Fernet.generate_key()
    store = CredentialStore(db_pool, encryption_key=key)
    db_pool.get_pool = AsyncMock(return_value=mock_asyncpg_pool)

    fernet = Fernet(key)
    plaintext = "secret-api-key"
    encrypted = fernet.encrypt(plaintext.encode()).decode()

    mock_row = {
        "credential_value": encrypted,
        "credential_type": "api_key",
        "platform": "anthropic",
        "platform_url": None,
        "is_encrypted": True,
        "expiry": None,
    }
    mock_asyncpg_pool.fetchrow = AsyncMock(return_value=mock_row)

    result = await store.get("test-key")

    assert result is not None
    assert result.value == plaintext


@pytest.mark.asyncio
async def test_get_raises_when_encrypted_but_no_key(credential_store_no_encryption, mock_asyncpg_pool):
    """get() raises CredentialError when encrypted but no key is set."""
    mock_row = {
        "credential_value": "encrypted-data",
        "credential_type": "api_key",
        "platform": "anthropic",
        "platform_url": None,
        "is_encrypted": True,
        "expiry": None,
    }
    mock_asyncpg_pool.fetchrow = AsyncMock(return_value=mock_row)

    with pytest.raises(CredentialError, match="encrypted but no CREDENTIAL_ENCRYPTION_KEY"):
        await credential_store_no_encryption.get("test-key")


@pytest.mark.asyncio
async def test_get_raises_when_decryption_fails(db_pool, mock_asyncpg_pool):
    """get() raises CredentialError when decryption fails."""
    key = Fernet.generate_key()
    store = CredentialStore(db_pool, encryption_key=key)
    db_pool.get_pool = AsyncMock(return_value=mock_asyncpg_pool)

    # Use a different key for encryption than decryption
    wrong_key = Fernet.generate_key()
    fernet_wrong = Fernet(wrong_key)
    encrypted_with_wrong_key = fernet_wrong.encrypt(b"test").decode()

    mock_row = {
        "credential_value": encrypted_with_wrong_key,
        "credential_type": "api_key",
        "platform": "anthropic",
        "platform_url": None,
        "is_encrypted": True,
        "expiry": None,
    }
    mock_asyncpg_pool.fetchrow = AsyncMock(return_value=mock_row)

    with pytest.raises(CredentialError, match="Failed to decrypt"):
        await store.get("test-key")


@pytest.mark.asyncio
async def test_put_calls_execute_with_correct_params(credential_store_no_encryption, mock_asyncpg_pool):
    """put() calls execute with correct parameters."""
    mock_asyncpg_pool.execute = AsyncMock()

    credential = Credential(
        value="test-value",
        credential_type=CredentialType.API_KEY,
        platform="anthropic",
        platform_url="https://api.anthropic.com",
        expiry=None,
    )

    await credential_store_no_encryption.put("test-key", credential)

    mock_asyncpg_pool.execute.assert_called_once()
    call_args = mock_asyncpg_pool.execute.call_args
    assert "INSERT INTO server_credentials" in call_args[0][0]
    assert call_args[0][1] == "test-key"  # name
    assert call_args[0][2] == "test-value"  # credential_value


@pytest.mark.asyncio
async def test_put_encrypts_when_fernet_set(db_pool, mock_asyncpg_pool):
    """put() encrypts value when fernet is set."""
    key = Fernet.generate_key()
    store = CredentialStore(db_pool, encryption_key=key)
    db_pool.get_pool = AsyncMock(return_value=mock_asyncpg_pool)
    mock_asyncpg_pool.execute = AsyncMock()

    credential = Credential(
        value="secret-value",
        credential_type=CredentialType.API_KEY,
        platform="anthropic",
        platform_url=None,
        expiry=None,
    )

    await store.put("test-key", credential)

    call_args = mock_asyncpg_pool.execute.call_args
    stored_value = call_args[0][2]  # credential_value param
    is_encrypted = call_args[0][6]  # is_encrypted param

    assert is_encrypted is True
    # Verify it's actually encrypted by trying to decrypt
    fernet = Fernet(key)
    decrypted = fernet.decrypt(stored_value.encode()).decode()
    assert decrypted == "secret-value"


@pytest.mark.asyncio
async def test_delete_returns_true_when_row_deleted(credential_store_no_encryption, mock_asyncpg_pool):
    """delete() returns True when row is deleted."""
    mock_asyncpg_pool.execute = AsyncMock(return_value="DELETE 1")

    result = await credential_store_no_encryption.delete("test-key")

    assert result is True


@pytest.mark.asyncio
async def test_delete_returns_false_when_no_row(credential_store_no_encryption, mock_asyncpg_pool):
    """delete() returns False when no row is deleted."""
    mock_asyncpg_pool.execute = AsyncMock(return_value="DELETE 0")

    result = await credential_store_no_encryption.delete("test-key")

    assert result is False


@pytest.mark.asyncio
async def test_delete_handles_multi_digit_counts(credential_store_no_encryption, mock_asyncpg_pool):
    """delete() correctly handles multi-digit deletion counts."""
    # While UNIQUE constraint prevents this on this table, the fix should be robust
    mock_asyncpg_pool.execute = AsyncMock(return_value="DELETE 10")

    result = await credential_store_no_encryption.delete("test-key")

    assert result is True


@pytest.mark.asyncio
async def test_list_names_returns_names(credential_store_no_encryption, mock_asyncpg_pool):
    """list_names() returns list of credential names."""
    mock_rows = [
        {"name": "key1"},
        {"name": "key2"},
        {"name": "key3"},
    ]
    mock_asyncpg_pool.fetch = AsyncMock(return_value=mock_rows)

    result = await credential_store_no_encryption.list_names()

    assert result == ["key1", "key2", "key3"]
    mock_asyncpg_pool.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_get_parses_expiry_datetime(credential_store_no_encryption, mock_asyncpg_pool):
    """get() correctly parses future expiry datetime."""
    expiry_dt = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    mock_row = {
        "credential_value": "test-value",
        "credential_type": "api_key",
        "platform": "anthropic",
        "platform_url": None,
        "is_encrypted": False,
        "expiry": expiry_dt,
    }
    mock_asyncpg_pool.fetchrow = AsyncMock(return_value=mock_row)

    result = await credential_store_no_encryption.get("test-key")

    assert result is not None
    assert result.expiry == expiry_dt


@pytest.mark.asyncio
async def test_get_parses_expiry_iso_string(credential_store_no_encryption, mock_asyncpg_pool):
    """get() correctly parses future expiry as ISO string (naive → UTC)."""
    iso_string = "2099-12-31T23:59:59"
    mock_row = {
        "credential_value": "test-value",
        "credential_type": "api_key",
        "platform": "anthropic",
        "platform_url": None,
        "is_encrypted": False,
        "expiry": iso_string,
    }
    mock_asyncpg_pool.fetchrow = AsyncMock(return_value=mock_row)

    result = await credential_store_no_encryption.get("test-key")

    assert result is not None
    assert isinstance(result.expiry, datetime)
    assert result.expiry.tzinfo is not None  # naive → UTC


@pytest.mark.asyncio
async def test_get_raises_on_expired_credential(credential_store_no_encryption, mock_asyncpg_pool):
    """get() raises CredentialError when credential has expired."""
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    mock_row = {
        "credential_value": "test-value",
        "credential_type": "api_key",
        "platform": "anthropic",
        "platform_url": None,
        "is_encrypted": False,
        "expiry": past,
    }
    mock_asyncpg_pool.fetchrow = AsyncMock(return_value=mock_row)

    with pytest.raises(CredentialError, match="has expired"):
        await credential_store_no_encryption.get("test-key")
