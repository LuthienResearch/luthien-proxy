"""Guard the passthrough-fallback safety invariant across every policy class.

A policy may set ``passthrough_fallback_safe = True`` only if it never modifies
requests. Otherwise the fallback could resend content the policy removed or
restricted (a redacted secret, a substituted model). This test fails when a
class opts in while overriding a request hook, so the invariant no longer
rests on a code comment.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import luthien_proxy.policies as policies_pkg
from luthien_proxy.policy_core.base_policy import BasePolicy

# Classes whose request hooks are pass-through defaults (return the request unchanged).
_DEFAULT_HOOK_OWNERS = {"BasePolicy", "AnthropicHookPolicy", "AnthropicExecutionInterface"}

# Explicit, reviewed exceptions: opted-in classes that may override a request hook.
_ALLOWLIST: set[str] = set()


def _all_policy_classes() -> list[type]:
    for mod in pkgutil.walk_packages(policies_pkg.__path__, policies_pkg.__name__ + "."):
        importlib.import_module(mod.name)
    seen: dict[str, type] = {}
    stack = list(BasePolicy.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        # Only shipped code: test helpers may opt in on purpose to exercise fallback.
        if cls.__module__.startswith("luthien_proxy."):
            seen[f"{cls.__module__}.{cls.__qualname__}"] = cls
    return list(seen.values())


def _hook_owner(cls: type, name: str) -> str | None:
    for klass in inspect.getmro(cls):
        if name in vars(klass):
            return klass.__name__
    return None


def _modifies_requests(cls: type) -> bool:
    owner = _hook_owner(cls, "on_anthropic_request")
    if owner is not None and owner not in _DEFAULT_HOOK_OWNERS:
        # SimplePolicy's on_anthropic_request only delegates to simple_on_request.
        if owner != "SimplePolicy":
            return True
    simple_owner = _hook_owner(cls, "simple_on_request")
    return simple_owner is not None and simple_owner != "SimplePolicy"


def test_opted_in_policies_never_modify_requests() -> None:
    offenders = [
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in _all_policy_classes()
        if getattr(cls, "passthrough_fallback_safe", False)
        and cls.__qualname__ not in _ALLOWLIST
        and _modifies_requests(cls)
    ]
    assert offenders == [], (
        "These policies set passthrough_fallback_safe=True but override a request hook, "
        f"so fallback could undo their request edits: {offenders}"
    )


def test_request_modifying_shipped_policies_are_fail_closed() -> None:
    """Sanity check the detector: a known request-rewriting policy is detected and not opted in."""
    from luthien_proxy.policies.string_replacement_policy import StringReplacementPolicy

    assert _modifies_requests(StringReplacementPolicy)
    assert StringReplacementPolicy.passthrough_fallback_safe is False
