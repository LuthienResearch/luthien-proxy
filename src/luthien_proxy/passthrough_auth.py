"""Passthrough auth dependency for /openai/, /gemini/, /anthropic/ routes.

Validates bearer tokens without building Anthropic-specific Credential objects.
The existing Anthropic auth chain (get_request_credential, verify_token) is
untouched — this is a parallel, simpler dep for passthrough routes only.
"""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from luthien_proxy.credential_manager import AuthMode, CredentialManager
from luthien_proxy.dependencies import get_api_key, get_credential_manager

_bearer = HTTPBearer(auto_error=False)


async def verify_passthrough_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(get_api_key),
    credential_manager: CredentialManager | None = Depends(get_credential_manager),
) -> str:
    """Validate bearer token for passthrough routes.

    Returns the raw token string on success.
    Raises 401 on invalid/missing token (when required by auth mode).

    Auth mode semantics:
    - PASSTHROUGH: any token accepted (client's own key forwarded upstream)
    - CLIENT_KEY: only the configured CLIENT_API_KEY is accepted
    - BOTH: CLIENT_API_KEY accepted, or any token (passthrough path)
    """
    token = credentials.credentials if credentials else None

    # Determine auth mode
    if credential_manager is None:
        auth_mode = AuthMode.CLIENT_KEY
    else:
        auth_mode = credential_manager.config.auth_mode

    if auth_mode == AuthMode.PASSTHROUGH:
        # Any token (or no token) is accepted; client's own key forwarded upstream
        return token or ""

    # CLIENT_KEY or BOTH mode: validate against configured CLIENT_API_KEY
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if api_key and secrets.compare_digest(token, api_key):
        return token

    if auth_mode == AuthMode.BOTH:
        # In BOTH mode, also accept any token (passthrough path)
        return token

    # CLIENT_KEY mode: token did not match
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )
