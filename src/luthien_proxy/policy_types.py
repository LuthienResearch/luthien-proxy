"""Policy type registry and synchronization.

Manages the policy_type table, which catalogs available built-in policy types.
Provides sync logic to upsert policy_type rows from an explicit allowlist and
mark missing ones as deprecated.

ABOUTME: Policy type management system. Exports REGISTERED_BUILTINS (source of truth),
sync_policy_types() (DB sync), and helper functions (derive_builtin_name, resolve_collisions).
"""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from luthien_proxy.admin.policy_discovery import extract_description
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)

REGISTERED_BUILTINS: tuple[str, ...] = (
    "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
    "luthien_proxy.policies.conversation_link_policy:ConversationLinkPolicy",
    "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy",
    "luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy",
    "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy",
    "luthien_proxy.policies.noop_policy:NoOpPolicy",
    "luthien_proxy.policies.onboarding_policy:OnboardingPolicy",
    "luthien_proxy.policies.presets.block_dangerous_commands:BlockDangerousCommandsPolicy",
    "luthien_proxy.policies.presets.block_sensitive_file_writes:BlockSensitiveFileWritesPolicy",
    "luthien_proxy.policies.presets.block_web_requests:BlockWebRequestsPolicy",
    "luthien_proxy.policies.presets.no_apologies:NoApologiesPolicy",
    "luthien_proxy.policies.presets.no_yapping:NoYappingPolicy",
    "luthien_proxy.policies.presets.plain_dashes:PlainDashesPolicy",
    "luthien_proxy.policies.presets.prefer_uv:PreferUvPolicy",
    "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy",
    "luthien_proxy.policies.simple_noop_policy:SimpleNoOpPolicy",
    "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
    "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
)


def derive_builtin_name(class_name: str) -> str:
    """Convert a policy class name to a kebab-case display id.

    Algorithm:
    1. Drop trailing 'Policy' if present
    2. Insert '-' between any lowercase followed by uppercase
    3. Lowercase

    Examples: SimpleLLMPolicy -> simple-llm, NoOpPolicy -> no-op, LLMPolicy -> llm.
    """
    # Drop 'Policy' suffix if present
    if class_name.endswith("Policy"):
        class_name = class_name[:-6]

    # Insert '-' between lowercase and uppercase
    kebab = re.sub(r"([a-z])([A-Z])", r"\1-\2", class_name)

    # Lowercase
    return kebab.lower()


def resolve_collisions(
    discovered: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """Assign a unique kebab name to each entry. Display-only; no FK semantics.

    Sort by class_ref lexicographically; first to claim a name wins; collisions get -2, -3, etc.
    Returns tuples in INPUT order.
    """
    # Create indexed entries to track original positions
    indexed_entries = [(i, entry) for i, entry in enumerate(discovered)]

    # Sort by class_ref lexicographically
    sorted_entries = sorted(indexed_entries, key=lambda x: x[1]["class_ref"])

    # Assign names with collision detection
    assigned_names: dict[str, int] = {}  # Maps base_name -> next suffix number to use
    result: list[tuple[str, dict[str, Any]]] = [None] * len(discovered)  # type: ignore[assignment]

    for original_idx, entry in sorted_entries:
        class_name = entry["name"]
        base_name = derive_builtin_name(class_name)

        # Check if this name is already taken
        if base_name not in assigned_names:
            # First to claim this name
            assigned_name = base_name
            assigned_names[base_name] = 2  # Next collision gets -2
        else:
            # Collision: append suffix
            suffix = assigned_names[base_name]
            assigned_name = f"{base_name}-{suffix}"
            assigned_names[base_name] = suffix + 1

        # Store result in original order
        result[original_idx] = (assigned_name, entry)

    return result


def _resolve_description(policy_class: type) -> str | None:
    """Description cascade: __policy_description__ attribute, then docstring, then None.

    Explicit branching — does NOT depend on extract_description's empty-string return.
    """
    explicit = getattr(policy_class, "__policy_description__", None)
    if explicit is not None:
        return explicit
    doc = extract_description(policy_class)
    if doc:
        return doc
    return None


async def sync_policy_types(
    pool: DatabasePool,
    *,
    class_refs: Iterable[str] = REGISTERED_BUILTINS,
) -> None:
    """Upsert policy_type rows from the registered built-in list.

    Marks any existing built-in row whose class_ref is not in `class_refs` as deprecated.

    Per-class import failures (ImportError, AttributeError, ValueError) are logged and skipped.
    Database-level exceptions propagate (table missing, constraint violations, connection errors are signal).
    """
    discovered = [{"class_ref": ref, "name": ref.split(":", 1)[1]} for ref in class_refs]
    resolved = resolve_collisions(discovered)
    now_iso = datetime.now(UTC).isoformat()
    seen_class_refs: list[str] = []

    async with pool.connection() as conn:
        for assigned_name, entry in resolved:
            class_ref = entry["class_ref"]
            try:
                module_path, class_name = class_ref.split(":", 1)
                module = importlib.import_module(module_path)
                policy_class = getattr(module, class_name)
            except (ImportError, AttributeError, ValueError) as exc:
                logger.warning(f"Skipping {class_ref}: {exc}")
                continue

            if not (isinstance(policy_class, type) and issubclass(policy_class, BasePolicy)):
                logger.warning(f"Skipping {class_ref}: not a BasePolicy subclass")
                continue

            description = _resolve_description(policy_class)

            # Partial-index-aware upsert: ON CONFLICT target matches the partial unique index.
            await conn.execute(
                """
                INSERT INTO policy_type
                    (name, description, definition_type, class_ref,
                     config_schema, deprecated, updated_at)
                VALUES ($1, $2, 'built-in', $3, $4, $5, $6)
                ON CONFLICT (class_ref) WHERE definition_type = 'built-in' DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    deprecated = EXCLUDED.deprecated,
                    updated_at = EXCLUDED.updated_at
                """,
                assigned_name,
                description,
                class_ref,
                None,
                False,
                now_iso,
            )
            seen_class_refs.append(class_ref)

        existing = await conn.fetch("SELECT class_ref FROM policy_type WHERE definition_type = 'built-in'")
        existing_refs = {row["class_ref"] for row in existing}
        to_deprecate = existing_refs - set(seen_class_refs)
        for ref in to_deprecate:
            await conn.execute(
                "UPDATE policy_type SET deprecated = $1 WHERE class_ref = $2",
                True,
                ref,
            )
