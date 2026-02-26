"""Admin API routes for dynamic policy management."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from luthien_proxy.admin.dynamic_loader import (
    PolicyLoadError,
    dry_run_load,
    load_policy_from_source,
)
from luthien_proxy.admin.policy_generator import generate_policy_code
from luthien_proxy.auth import verify_admin_token
from luthien_proxy.dependencies import get_db_pool, get_policy_manager
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.utils import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/policies", tags=["dynamic-policies"])


# === Request / Response Models ===


class GenerateRequest(BaseModel):
    """Request to generate a policy from a natural language prompt."""

    prompt: str = Field(..., description="Natural language description of the desired policy")


class GenerateResponse(BaseModel):
    """Response containing generated policy code."""

    code: str
    model: str
    prompt: str


class SaveRequest(BaseModel):
    """Request to save a dynamic policy."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    source_code: str = Field(..., min_length=10)
    config: dict[str, Any] = Field(default_factory=dict)
    prompt: str | None = None


class PolicySummary(BaseModel):
    """Summary of a saved dynamic policy."""

    id: str
    name: str
    description: str | None
    is_active: bool
    version: int
    created_at: str
    updated_at: str
    created_by: str | None


class PolicyDetail(BaseModel):
    """Full details of a saved dynamic policy."""

    id: str
    name: str
    description: str | None
    source_code: str
    config: dict[str, Any]
    prompt: str | None
    is_active: bool
    version: int
    created_at: str
    updated_at: str
    created_by: str | None


class ValidateRequest(BaseModel):
    """Request to validate policy code without saving."""

    source_code: str
    config: dict[str, Any] = Field(default_factory=dict)


class ValidateResponse(BaseModel):
    """Result of policy validation."""

    valid: bool
    issues: list[str] = Field(default_factory=list)
    class_name: str | None = None
    short_name: str | None = None


# === Endpoints ===


@router.post("/generate", response_model=GenerateResponse)
async def generate_policy(
    body: GenerateRequest,
    _: str = Depends(verify_admin_token),
):
    """Generate policy code from a natural language prompt using an LLM."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY not configured")

    try:
        result = await generate_policy_code(body.prompt, api_key)
        return GenerateResponse(code=result["code"], model=result["model"], prompt=body.prompt)
    except Exception as e:
        logger.error(f"Policy generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")


@router.post("/validate", response_model=ValidateResponse)
async def validate_policy(
    body: ValidateRequest,
    _: str = Depends(verify_admin_token),
):
    """Validate policy code without saving. Checks syntax, safety, and instantiation."""
    result = dry_run_load(body.source_code, body.config or None)
    return ValidateResponse(
        valid=result["valid"],
        issues=result.get("issues", []),
        class_name=result.get("class_name"),
        short_name=result.get("short_name"),
    )


@router.post("/save", response_model=PolicyDetail)
async def save_policy(
    body: SaveRequest,
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """Save a dynamic policy to the database.

    Validates the code first — rejects if validation fails.
    """
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    # Validate before saving
    validation = dry_run_load(body.source_code, body.config or None)
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=f"Validation failed: {'; '.join(validation['issues'])}")

    pool = await db_pool.get_pool()
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO dynamic_policies (name, description, source_code, config, prompt, created_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, name, description, source_code, config, prompt, is_active, version,
                      created_at, updated_at, created_by
            """,
            body.name,
            body.description,
            body.source_code,
            json.dumps(body.config),
            body.prompt,
            "admin-api",
        )
    except Exception as e:
        error_msg = str(e)
        if "unique" in error_msg.lower() or "duplicate" in error_msg.lower():
            raise HTTPException(status_code=409, detail=f"Policy with name '{body.name}' already exists")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    return _row_to_detail(row)


@router.get("/", response_model=list[PolicySummary])
async def list_policies(
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """List all saved dynamic policies."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    pool = await db_pool.get_pool()
    rows = await pool.fetch(
        """
        SELECT id, name, description, is_active, version, created_at, updated_at, created_by
        FROM dynamic_policies
        ORDER BY created_at DESC
        """
    )
    return [
        PolicySummary(
            id=str(r["id"]),
            name=str(r["name"]),
            description=str(r["description"]) if r["description"] else None,
            is_active=bool(r["is_active"]),
            version=int(str(r["version"])),
            created_at=r["created_at"].isoformat(),  # type: ignore[union-attr]
            updated_at=r["updated_at"].isoformat(),  # type: ignore[union-attr]
            created_by=str(r["created_by"]) if r["created_by"] else None,
        )
        for r in rows
    ]


@router.get("/{policy_id}", response_model=PolicyDetail)
async def get_policy(
    policy_id: UUID,
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """Get full details of a saved dynamic policy."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    pool = await db_pool.get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, name, description, source_code, config, prompt, is_active, version,
               created_at, updated_at, created_by
        FROM dynamic_policies
        WHERE id = $1
        """,
        policy_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _row_to_detail(row)


@router.post("/{policy_id}/activate")
async def activate_policy(
    policy_id: UUID,
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
    manager: PolicyManager = Depends(get_policy_manager),
):
    """Activate a saved dynamic policy, making it the live policy."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    pool = await db_pool.get_pool()
    row = await pool.fetchrow(
        "SELECT source_code, config, name FROM dynamic_policies WHERE id = $1",
        policy_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Policy not found")

    source_code = str(row["source_code"])
    config_value = row["config"]
    config = config_value if isinstance(config_value, dict) else json.loads(str(config_value))
    name = str(row["name"])

    # Load the policy from source code
    try:
        policy = load_policy_from_source(source_code, config, policy_name=name)
    except PolicyLoadError as e:
        raise HTTPException(status_code=400, detail=f"Failed to load policy: {e}")

    # Hot-swap via policy manager
    manager.set_dynamic_policy(policy)  # type: ignore[arg-type]

    # Persist activation atomically via a connection transaction
    policy_class_name = policy.__class__.__name__
    async with db_pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE dynamic_policies SET is_active = FALSE, updated_at = NOW() WHERE is_active = TRUE AND id != $1",
                policy_id,
            )
            await conn.execute(
                "UPDATE dynamic_policies SET is_active = TRUE, updated_at = NOW() WHERE id = $1",
                policy_id,
            )
            await conn.execute(
                """
                INSERT INTO current_policy (id, policy_class_ref, config, enabled_at, enabled_by)
                VALUES (1, $1, $2, NOW(), $3)
                ON CONFLICT (id) DO UPDATE SET
                    policy_class_ref = EXCLUDED.policy_class_ref,
                    config = EXCLUDED.config,
                    enabled_at = EXCLUDED.enabled_at,
                    enabled_by = EXCLUDED.enabled_by
                """,
                f"dynamic:{policy_class_name}",
                json.dumps(config),
                f"dynamic-policy:{name}",
            )

    return {"success": True, "message": f"Policy '{name}' activated", "policy": policy_class_name}


@router.delete("/{policy_id}")
async def delete_policy(
    policy_id: UUID,
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """Delete a saved dynamic policy."""
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")

    pool = await db_pool.get_pool()

    # Check if active
    row = await pool.fetchrow("SELECT is_active, name FROM dynamic_policies WHERE id = $1", policy_id)
    if not row:
        raise HTTPException(status_code=404, detail="Policy not found")
    if row["is_active"]:
        raise HTTPException(status_code=400, detail="Cannot delete an active policy. Deactivate it first.")

    await pool.execute("DELETE FROM dynamic_policies WHERE id = $1", policy_id)
    return {"success": True, "message": f"Policy '{row['name']}' deleted"}


# === Helpers ===


def _row_to_detail(row: Any) -> PolicyDetail:
    """Convert a database row to a PolicyDetail response."""
    config_value = row["config"]
    config = config_value if isinstance(config_value, dict) else json.loads(str(config_value))

    return PolicyDetail(
        id=str(row["id"]),
        name=str(row["name"]),
        description=str(row["description"]) if row["description"] else None,
        source_code=str(row["source_code"]),
        config=config,
        prompt=str(row["prompt"]) if row["prompt"] else None,
        is_active=bool(row["is_active"]),
        version=int(row["version"]),
        created_at=row["created_at"].isoformat(),  # type: ignore[union-attr]
        updated_at=row["updated_at"].isoformat(),  # type: ignore[union-attr]
        created_by=str(row["created_by"]) if row["created_by"] else None,
    )


__all__ = ["router"]
