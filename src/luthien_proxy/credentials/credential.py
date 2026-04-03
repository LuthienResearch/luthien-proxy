"""Credential value object and type enum.

A Credential carries a secret value alongside its type metadata so the
rest of the system can forward it correctly without re-inspecting headers
or guessing from string prefixes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class CredentialType(str, Enum):
    """Credential type — matches AnthropicClient's auth_type vocabulary."""

    API_KEY = "api_key"
    AUTH_TOKEN = "auth_token"


@dataclass(frozen=True)
class Credential:
    """A credential that can authenticate against an LLM provider.

    Type-agnostic until the HTTP client layer, which inspects
    credential_type to set the right headers.
    """

    value: str
    credential_type: CredentialType
    platform: str = "anthropic"
    platform_url: str | None = None
    expiry: datetime | None = None

    def __repr__(self) -> str:
        """Mask the credential value to prevent accidental secret leakage in logs/tracebacks."""
        masked = self.value[:4] + "..." if len(self.value) > 4 else "***"
        return f"Credential(value='{masked}', credential_type={self.credential_type!r}, platform={self.platform!r})"


class CredentialError(Exception):
    """Raised when credential resolution fails."""
