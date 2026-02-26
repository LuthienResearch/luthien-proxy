"""Dynamic policy loader: compile and instantiate policies from Python source code."""

from __future__ import annotations

import ast
import logging
from typing import Any

from luthien_proxy.policy_core.base_policy import BasePolicy

logger = logging.getLogger(__name__)

# Modules that dynamic policies are allowed to import
ALLOWED_IMPORTS = frozenset(
    {
        "asyncio",
        "json",
        "re",
        "logging",
        "copy",
        "dataclasses",
        "typing",
        "pydantic",
        "luthien_proxy.policy_core",
        "luthien_proxy.policy_core.base_policy",
        "luthien_proxy.policy_core.openai_interface",
        "luthien_proxy.policy_core.anthropic_interface",
        "luthien_proxy.policy_core.policy_context",
        "luthien_proxy.policy_core.streaming_policy_context",
        "luthien_proxy.policy_core.chunk_builder",
        "litellm",
    }
)

# Names that must never appear in dynamic policy source
BLOCKED_NAMES = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "__import__",
        "globals",
        "locals",
        "breakpoint",
        "exit",
        "quit",
        "open",
        "subprocess",
        "os",
        "sys",
        "shutil",
        "pathlib",
        "importlib",
        "ctypes",
        "socket",
        "pickle",
        "shelve",
        "multiprocessing",
        "threading",
    }
)


class PolicyLoadError(Exception):
    """Raised when a dynamic policy cannot be loaded."""


class PolicyValidationError(PolicyLoadError):
    """Raised when policy source code fails validation."""


def validate_source(source_code: str) -> list[str]:
    """Validate policy source code for syntax and safety.

    Returns a list of issues found. Empty list means the code is valid.
    """
    issues: list[str] = []

    # Syntax check
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        issues.append(f"Syntax error at line {e.lineno}: {e.msg}")
        return issues

    # Walk the AST looking for disallowed constructs
    for node in ast.walk(tree):
        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _is_import_allowed(alias.name):
                    issues.append(f"Disallowed import: '{alias.name}'")

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not _is_import_allowed(module):
                issues.append(f"Disallowed import: 'from {module}'")

        # Check for blocked built-in calls
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            issues.append(f"Disallowed name: '{node.id}'")

        elif isinstance(node, ast.Attribute) and node.attr in BLOCKED_NAMES:
            issues.append(f"Disallowed attribute access: '.{node.attr}'")

    # Must contain at least one class definition
    class_defs = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if not class_defs:
        issues.append("No class definition found — policy must define a class")

    return issues


def _is_import_allowed(module_name: str) -> bool:
    """Check if a module import is allowed for dynamic policies."""
    for allowed in ALLOWED_IMPORTS:
        if module_name == allowed or module_name.startswith(allowed + "."):
            return True
    return False


def load_policy_from_source(
    source_code: str,
    config: dict[str, Any] | None = None,
    policy_name: str = "<dynamic>",
) -> BasePolicy:
    """Compile Python source and instantiate the policy class.

    The source code must define exactly one class that inherits from BasePolicy.
    The class is instantiated with the provided config (if any).

    Raises:
        PolicyValidationError: If source fails validation
        PolicyLoadError: If compilation or instantiation fails
    """
    issues = validate_source(source_code)
    if issues:
        raise PolicyValidationError(f"Validation failed for '{policy_name}': {'; '.join(issues)}")

    # Compile
    try:
        code = compile(source_code, f"<dynamic-policy:{policy_name}>", "exec")
    except Exception as e:
        raise PolicyLoadError(f"Compilation failed: {e}") from e

    # Execute in a namespace that has access to the policy framework
    namespace: dict[str, Any] = {}
    try:
        exec(code, namespace)  # noqa: S102
    except Exception as e:
        raise PolicyLoadError(f"Execution failed: {e}") from e

    # Find BasePolicy subclasses
    policy_classes = [
        v
        for v in namespace.values()
        if isinstance(v, type) and issubclass(v, BasePolicy) and v is not BasePolicy
    ]

    if not policy_classes:
        raise PolicyLoadError(
            "No BasePolicy subclass found in source code. "
            "Ensure your class inherits from BasePolicy."
        )

    if len(policy_classes) > 1:
        names = [c.__name__ for c in policy_classes]
        logger.warning(f"Multiple policy classes found: {names}. Using first: {names[0]}")

    policy_class = policy_classes[0]

    # Instantiate
    try:
        if config:
            return policy_class(config=config)
        return policy_class()
    except TypeError:
        # Try without config kwarg
        if config:
            try:
                return policy_class(**config)
            except TypeError as e:
                raise PolicyLoadError(f"Could not instantiate {policy_class.__name__} with config: {e}") from e
        raise


def dry_run_load(source_code: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate and attempt to load a policy without keeping the instance.

    Returns a dict with validation results.
    """
    issues = validate_source(source_code)
    if issues:
        return {"valid": False, "issues": issues}

    try:
        policy = load_policy_from_source(source_code, config, policy_name="<dry-run>")
        return {
            "valid": True,
            "issues": [],
            "class_name": policy.__class__.__name__,
            "short_name": policy.short_policy_name,
        }
    except PolicyLoadError as e:
        return {"valid": False, "issues": [str(e)]}


__all__ = [
    "PolicyLoadError",
    "PolicyValidationError",
    "validate_source",
    "load_policy_from_source",
    "dry_run_load",
]
