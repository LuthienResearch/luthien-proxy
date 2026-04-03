"""DB-backed storage for operator-provisioned credentials.

Internal to CredentialManager — not a public interface. Policies and gateway
code interact with CredentialManager, never with CredentialStore directly.
"""

from __future__ import annotations

import binascii
import logging
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from luthien_proxy.credentials.credential import Credential, CredentialError, CredentialType
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


class CredentialStore:
    """DB-backed storage for operator-provisioned credentials."""

    def __init__(self, db_pool: DatabasePool, encryption_key: bytes | None = None) -> None:
        """Initialize with database pool and optional encryption key.

        Args:
            db_pool: Database pool for credential storage.
            encryption_key: Optional Fernet key for encrypting credential values at rest.
        """
        self._db = db_pool
        if encryption_key:
            try:
                self._fernet = Fernet(encryption_key)
            except (ValueError, binascii.Error) as exc:
                raise CredentialError(
                    f"Invalid CREDENTIAL_ENCRYPTION_KEY: {exc}. "
                    "Generate a valid key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                ) from exc
        else:
            self._fernet = None

    async def get(self, name: str) -> Credential | None:
        """Retrieve a stored credential by name. Returns None if not found."""
        pool = await self._db.get_pool()
        row = await pool.fetchrow(
            "SELECT credential_value, credential_type, platform, platform_url, "
            "is_encrypted, expiry FROM server_credentials WHERE name = $1",
            name,
        )
        if row is None:
            return None

        raw_value = str(row["credential_value"])
        is_encrypted = bool(row["is_encrypted"])

        if is_encrypted:
            if self._fernet is None:
                raise CredentialError(f"Server key '{name}' is encrypted but no CREDENTIAL_ENCRYPTION_KEY is set")
            try:
                raw_value = self._fernet.decrypt(raw_value.encode()).decode()
            except InvalidToken as exc:
                raise CredentialError(f"Failed to decrypt server key '{name}': wrong encryption key?") from exc

        expiry_raw = row["expiry"]
        expiry: datetime | None = None
        if expiry_raw is not None:
            expiry = expiry_raw if isinstance(expiry_raw, datetime) else datetime.fromisoformat(str(expiry_raw))

        platform_url = row["platform_url"]
        return Credential(
            value=raw_value,
            credential_type=CredentialType(str(row["credential_type"])),
            platform=str(row["platform"]),
            platform_url=str(platform_url) if platform_url is not None else None,
            expiry=expiry,
        )

    async def put(self, name: str, credential: Credential) -> None:
        """Create or update a stored credential."""
        value = credential.value
        is_encrypted = False

        if self._fernet is not None:
            value = self._fernet.encrypt(value.encode()).decode()
            is_encrypted = True

        now = datetime.now(timezone.utc).isoformat()
        pool = await self._db.get_pool()
        await pool.execute(
            """
            INSERT INTO server_credentials
                (name, credential_value, credential_type, platform, platform_url,
                 is_encrypted, expiry, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
            ON CONFLICT (name) DO UPDATE SET
                credential_value = EXCLUDED.credential_value,
                credential_type = EXCLUDED.credential_type,
                platform = EXCLUDED.platform,
                platform_url = EXCLUDED.platform_url,
                is_encrypted = EXCLUDED.is_encrypted,
                expiry = EXCLUDED.expiry,
                updated_at = EXCLUDED.updated_at
            """,
            name,
            value,
            credential.credential_type.value,
            credential.platform,
            credential.platform_url,
            is_encrypted,
            credential.expiry.isoformat() if credential.expiry else None,
            now,
        )

    async def delete(self, name: str) -> bool:
        """Delete a stored credential. Returns True if it existed."""
        pool = await self._db.get_pool()
        result = await pool.execute("DELETE FROM server_credentials WHERE name = $1", name)
        # asyncpg returns "DELETE N" where N is the count
        count_str = str(result).rsplit(" ", 1)[-1]
        return count_str != "0"

    async def list_names(self) -> list[str]:
        """List all stored credential names (no values)."""
        pool = await self._db.get_pool()
        rows = await pool.fetch("SELECT name FROM server_credentials ORDER BY name")
        return [str(row["name"]) for row in rows]
