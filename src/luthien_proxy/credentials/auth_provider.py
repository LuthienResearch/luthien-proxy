"""Inference-provider reference types for policy credential configuration.

Policies declare how to obtain their judge-side inference target via these
tagged types. The YAML field is `inference_provider:` as of PR #609;
the pre-PR-#609 `auth_provider:` field is still accepted with a deprecation
warning so deployed configs don't break on upgrade.

The new reference shapes (Model A) are:

- `inference_provider: user_credentials`
- `inference_provider: {provider: "registry-name"}`
- `inference_provider: {user_then_provider: {name: "registry-name", on_fallback: "warn"}}`

The legacy shapes still accepted:

- `auth_provider: user_credentials`               (unchanged)
- `auth_provider: {server_key: "name"}`           (aliases to `{provider: "name"}`)
- `auth_provider: {user_then_server: "name"}`     (aliases to `{user_then_provider: {name: ...}}`)
- `auth_provider: {user_then_server: {name: ..., on_fallback: ...}}`

A stable internal representation (`UserCredentials` / `Provider` /
`UserThenProvider`) is produced by `parse_inference_provider()` regardless
of which YAML shape was used. `parse_auth_provider` is retained as an alias
that additionally emits a deprecation warning and accepts the legacy
inner-key names.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserCredentials:
    """Extract from current request headers. Fail if absent."""


@dataclass(frozen=True)
class Provider:
    """Look up a named inference provider in the `InferenceProviderRegistry`.

    The registry row carries its own backend config + credential reference.
    This is the replacement for the pre-PR-#609 `ServerKey` reference, which
    pointed at a credential directly; PR #607 introduced a separate
    providers table so judges can target a named *provider* (HTTP backend
    + default model + credential reference) instead of a raw credential.
    """

    name: str


@dataclass(frozen=True)
class UserThenProvider:
    """Try user creds from request, fall back to named provider.

    on_fallback controls behavior when the request lacks a user credential:

    - ``fallback``: silently use the named provider
    - ``warn``: use the named provider, log a warning + emit metric (default)
    - ``fail``: reject the request (strictest)
    """

    name: str
    on_fallback: Literal["fallback", "warn", "fail"] = "warn"


#: Back-compat aliases for pre-PR-#609 callers that imported these names.
#:
#: ``ServerKey`` used to mean "look up a credential by this name"; the new
#: ``Provider`` means "look up an inference-provider (credential + backend
#: config) by this name" against the registry added in PR #607. Runtime
#: resolution lives in the dispatch layer, which only sees ``Provider`` /
#: ``UserThenProvider`` — the parser collapses the legacy key names at the
#: YAML layer so the internal representation stays single-valued.
ServerKey = Provider
UserThenServer = UserThenProvider


InferenceProviderRef = UserCredentials | Provider | UserThenProvider
#: Legacy name kept for callers that haven't migrated yet.
AuthProvider = InferenceProviderRef


_VALID_FALLBACK_MODES = frozenset({"fallback", "warn", "fail"})


def parse_inference_provider(raw: str | dict | None) -> InferenceProviderRef:
    """Parse an ``inference_provider`` value from YAML/dict config.

    Accepts (new shape):

    - ``None`` or ``"user_credentials"`` -> ``UserCredentials()``
    - ``{"provider": "name"}`` -> ``Provider(name)``
    - ``{"user_then_provider": "name"}`` -> ``UserThenProvider(name)``
    - ``{"user_then_provider": {"name": "x", "on_fallback": "fail"}}``
      -> ``UserThenProvider(name="x", on_fallback="fail")``

    Also accepts the legacy inner-key names (`server_key`, `user_then_server`)
    emitted before PR #609. Legacy inner keys are silently rewritten to the
    new shape here — the deprecation warning for the outer YAML field name
    is emitted by `parse_auth_provider`, which is the only caller that sees
    the legacy outer name.
    """
    if raw is None or raw == "user_credentials":
        return UserCredentials()
    if isinstance(raw, dict):
        # New inner-key names
        if "provider" in raw:
            name = raw["provider"]
            if not isinstance(name, str):
                raise ValueError(f"provider name must be a string, got {type(name).__name__}")
            return Provider(name=name)
        if "user_then_provider" in raw:
            return _parse_user_then(raw["user_then_provider"], key="user_then_provider")
        # Legacy inner-key names
        if "server_key" in raw:
            name = raw["server_key"]
            if not isinstance(name, str):
                raise ValueError(f"server_key name must be a string, got {type(name).__name__}")
            return Provider(name=name)
        if "user_then_server" in raw:
            return _parse_user_then(raw["user_then_server"], key="user_then_server")
    raise ValueError(f"Unknown inference_provider: {raw}")


def _parse_user_then(val: object, *, key: str) -> UserThenProvider:
    if isinstance(val, str):
        return UserThenProvider(name=val)
    if isinstance(val, dict):
        if "name" not in val:
            raise ValueError(f"{key} dict config must include 'name'")
        name = val["name"]
        if not isinstance(name, str):
            raise ValueError(f"{key} name must be a string, got {type(name).__name__}")
        on_fallback = val.get("on_fallback", "warn")
        if on_fallback not in _VALID_FALLBACK_MODES:
            raise ValueError(
                f"Invalid on_fallback: {on_fallback!r}. Must be one of: {', '.join(sorted(_VALID_FALLBACK_MODES))}"
            )
        return UserThenProvider(name=name, on_fallback=on_fallback)
    raise ValueError(f"{key} value must be a string or dict, got {type(val).__name__}")


def parse_auth_provider(raw: str | dict | None) -> InferenceProviderRef:
    """Legacy alias for `parse_inference_provider` with a deprecation warning.

    Emits a one-off warning recommending callers rename the YAML field from
    `auth_provider` to `inference_provider`. The old inner-key names
    (`server_key`, `user_then_server`) are still parsed transparently —
    operators with deployed configs don't need to change anything urgently,
    they just see a log line until they migrate.
    """
    logger.warning(
        "Policy config uses 'auth_provider'; rename to 'inference_provider'. "
        "The old field name will be removed in a follow-up release."
    )
    return parse_inference_provider(raw)
