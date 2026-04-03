"""Auth provider types for policy credential configuration.

Policies declare how to obtain credentials via these tagged types,
which are parsed from YAML config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class UserCredentials:
    """Extract from current request headers. Fail if absent."""


@dataclass(frozen=True)
class ServerKey:
    """Look up operator-provisioned key by name from persistent store."""

    name: str


@dataclass(frozen=True)
class UserThenServer:
    """Try user creds from request, fall back to named server key.

    on_fallback controls behavior when user credential is missing:
    - "fallback": silently use server key
    - "warn": use server key but log a warning + emit metric (default)
    - "fail": reject the request (strictest)
    """

    name: str
    on_fallback: Literal["fallback", "warn", "fail"] = "warn"


AuthProvider = UserCredentials | ServerKey | UserThenServer


_VALID_FALLBACK_MODES = frozenset({"fallback", "warn", "fail"})


def parse_auth_provider(raw: str | dict | None) -> AuthProvider:
    """Parse an auth_provider value from YAML/dict config.

    Accepts:
    - None or "user_credentials" -> UserCredentials()
    - {"server_key": "name"} -> ServerKey(name)
    - {"user_then_server": "name"} -> UserThenServer(name)
    - {"user_then_server": {"name": "x", "on_fallback": "fail"}} -> UserThenServer(name="x", on_fallback="fail")
    """
    if raw is None or raw == "user_credentials":
        return UserCredentials()
    if isinstance(raw, dict):
        if "server_key" in raw:
            name = raw["server_key"]
            if not isinstance(name, str):
                raise ValueError(f"server_key name must be a string, got {type(name).__name__}")
            return ServerKey(name=name)
        if "user_then_server" in raw:
            val = raw["user_then_server"]
            if isinstance(val, str):
                return UserThenServer(name=val)
            if isinstance(val, dict):
                name = val["name"]
                if not isinstance(name, str):
                    raise ValueError(f"user_then_server name must be a string, got {type(name).__name__}")
                on_fallback = val.get("on_fallback", "warn")
                if on_fallback not in _VALID_FALLBACK_MODES:
                    raise ValueError(
                        f"Invalid on_fallback: {on_fallback!r}. Must be one of: {', '.join(sorted(_VALID_FALLBACK_MODES))}"
                    )
                return UserThenServer(name=name, on_fallback=on_fallback)
    raise ValueError(f"Unknown auth_provider: {raw}")
