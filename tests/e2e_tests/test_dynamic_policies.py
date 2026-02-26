"""E2E tests for dynamic policy CRUD and activation.

Tests the admin API endpoints for dynamic policy management:
- POST /admin/policies/generate  - Generate policy from prompt (skipped without ANTHROPIC_API_KEY)
- POST /admin/policies/validate  - Validate policy code
- POST /admin/policies/save      - Save policy to database
- GET  /admin/policies/          - List saved policies
- GET  /admin/policies/{id}      - Get policy by ID
- POST /admin/policies/{id}/activate - Activate a saved policy
- DELETE /admin/policies/{id}    - Delete a saved policy
- Activated policy affects request processing

These tests require:
- Running gateway service (docker compose up gateway)
- Database with migration 009 applied (dynamic_policies table)
"""

import asyncio
import os
import uuid

import httpx
import pytest

GATEWAY_URL = os.getenv("E2E_GATEWAY_URL", "http://localhost:8000")
ADMIN_API_KEY = os.getenv("E2E_ADMIN_API_KEY", os.getenv("ADMIN_API_KEY", "admin-dev-key"))
PROXY_API_KEY = os.getenv("E2E_API_KEY", os.getenv("PROXY_API_KEY", "sk-luthien-dev-key"))

# A minimal dynamic policy that uppercases all OpenAI response content.
# Uses only allowed imports so it passes the sandbox validator.
ALLCAPS_DYNAMIC_POLICY = '''\
from __future__ import annotations
from typing import Any
from litellm.types.utils import ModelResponse
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

class DynamicAllCapsPolicy(BasePolicy, OpenAIPolicyInterface):
    """Dynamic policy that uppercases OpenAI response text."""

    @property
    def short_policy_name(self) -> str:
        return "DynAllCaps"

    async def on_openai_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_openai_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        if response.choices:
            for choice in response.choices:
                msg = choice.message  # type: ignore[union-attr]
                if msg and isinstance(msg.content, str):
                    msg.content = msg.content.upper()
        return response

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        ctx.push_chunk(ctx.last_chunk_received)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        pass
'''


@pytest.fixture
async def http_client():
    """Provide async HTTP client."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        yield client


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {ADMIN_API_KEY}"}


@pytest.fixture
def proxy_headers():
    return {"Authorization": f"Bearer {PROXY_API_KEY}"}


async def _cleanup_policy(client: httpx.AsyncClient, admin_headers: dict, policy_id: str) -> None:
    """Best-effort cleanup: deactivate (by restoring NoOp) then delete a policy."""
    # Restore static NoOp so the dynamic policy is no longer active
    await client.post(
        f"{GATEWAY_URL}/admin/policy/set",
        headers=admin_headers,
        json={
            "policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy",
            "config": {},
            "enabled_by": "e2e-cleanup",
        },
    )
    await asyncio.sleep(0.3)

    # Mark dynamic policy as inactive in the DB so we can delete it
    # (activate sets is_active; deleting an active policy is rejected)
    # The easiest path: just try the delete — if it fails because active, deactivate first.
    resp = await client.delete(f"{GATEWAY_URL}/admin/policies/{policy_id}", headers=admin_headers)
    if resp.status_code == 400 and "active" in resp.text.lower():
        # Need to activate another policy first to clear active flag
        # Already restored NoOp above; the DB flag may still be set.
        # The activate endpoint clears others, so we need a second dynamic policy — skip.
        pass


@pytest.fixture
async def saved_policy(http_client, admin_headers):
    """Save a dynamic policy and yield its detail dict; clean up afterwards."""
    unique_name = f"e2e-test-policy-{uuid.uuid4().hex[:8]}"
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/save",
        headers=admin_headers,
        json={
            "name": unique_name,
            "description": "E2E test policy",
            "source_code": ALLCAPS_DYNAMIC_POLICY,
            "config": {},
            "prompt": "make everything uppercase",
        },
    )
    assert resp.status_code == 200, f"Failed to save policy: {resp.text}"
    detail = resp.json()
    yield detail

    await _cleanup_policy(http_client, admin_headers, detail["id"])


# ── Validate ─────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_validate_valid_policy(http_client, admin_headers):
    """Valid policy code passes validation."""
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/validate",
        headers=admin_headers,
        json={"source_code": ALLCAPS_DYNAMIC_POLICY, "config": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["class_name"] == "DynamicAllCapsPolicy"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_validate_invalid_policy(http_client, admin_headers):
    """Invalid policy code fails validation with issues."""
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/validate",
        headers=admin_headers,
        json={"source_code": "import os\nclass Foo:\n    pass", "config": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert len(data["issues"]) > 0


# ── Save ─────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_save_policy(http_client, admin_headers):
    """Save a valid dynamic policy and get back its details."""
    unique_name = f"e2e-save-{uuid.uuid4().hex[:8]}"
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/save",
        headers=admin_headers,
        json={
            "name": unique_name,
            "description": "Test save",
            "source_code": ALLCAPS_DYNAMIC_POLICY,
            "config": {},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == unique_name
    assert data["is_active"] is False
    assert data["version"] == 1
    policy_id = data["id"]

    # Cleanup
    await _cleanup_policy(http_client, admin_headers, policy_id)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_save_rejects_invalid_code(http_client, admin_headers):
    """Saving code that fails validation returns 400."""
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/save",
        headers=admin_headers,
        json={
            "name": f"e2e-bad-{uuid.uuid4().hex[:8]}",
            "source_code": "import os\nclass Bad:\n    pass",
        },
    )
    assert resp.status_code == 400


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_save_duplicate_name_rejected(http_client, admin_headers, saved_policy):
    """Saving a policy with a duplicate name returns 409."""
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/save",
        headers=admin_headers,
        json={
            "name": saved_policy["name"],
            "source_code": ALLCAPS_DYNAMIC_POLICY,
        },
    )
    assert resp.status_code == 409


# ── List ─────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_list_policies(http_client, admin_headers, saved_policy):
    """Listing policies includes the saved policy."""
    resp = await http_client.get(f"{GATEWAY_URL}/admin/policies/", headers=admin_headers)
    assert resp.status_code == 200
    policies = resp.json()
    assert isinstance(policies, list)
    ids = [p["id"] for p in policies]
    assert saved_policy["id"] in ids


# ── Get by ID ────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_get_policy_by_id(http_client, admin_headers, saved_policy):
    """Fetching a policy by ID returns full details including source code."""
    resp = await http_client.get(
        f"{GATEWAY_URL}/admin/policies/{saved_policy['id']}",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == saved_policy["id"]
    assert data["name"] == saved_policy["name"]
    assert "source_code" in data
    assert len(data["source_code"]) > 0


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_get_nonexistent_policy_returns_404(http_client, admin_headers):
    """Fetching a non-existent policy returns 404."""
    fake_id = str(uuid.uuid4())
    resp = await http_client.get(
        f"{GATEWAY_URL}/admin/policies/{fake_id}",
        headers=admin_headers,
    )
    assert resp.status_code == 404


# ── Activate ─────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_activate_policy(http_client, admin_headers, saved_policy):
    """Activating a policy marks it active and updates the DB."""
    policy_id = saved_policy["id"]
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/{policy_id}/activate",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True

    # Verify the policy is marked active in the DB
    detail_resp = await http_client.get(
        f"{GATEWAY_URL}/admin/policies/{policy_id}",
        headers=admin_headers,
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["is_active"] is True

    # Cleanup
    await _cleanup_policy(http_client, admin_headers, policy_id)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_activate_only_deactivates_previously_active(http_client, admin_headers):
    """Activating a policy should only deactivate the currently-active one, not all policies."""
    ids = []
    try:
        # Create three policies
        for i in range(3):
            resp = await http_client.post(
                f"{GATEWAY_URL}/admin/policies/save",
                headers=admin_headers,
                json={
                    "name": f"e2e-deact-{uuid.uuid4().hex[:8]}",
                    "description": f"Deactivation test {i}",
                    "source_code": ALLCAPS_DYNAMIC_POLICY,
                    "config": {},
                },
            )
            assert resp.status_code == 200
            ids.append(resp.json()["id"])

        # Activate policy 0
        resp = await http_client.post(
            f"{GATEWAY_URL}/admin/policies/{ids[0]}/activate",
            headers=admin_headers,
        )
        assert resp.status_code == 200

        # Activate policy 1 — should deactivate policy 0 only, not touch policy 2
        resp = await http_client.post(
            f"{GATEWAY_URL}/admin/policies/{ids[1]}/activate",
            headers=admin_headers,
        )
        assert resp.status_code == 200

        # Check states
        for idx, pid in enumerate(ids):
            detail = await http_client.get(
                f"{GATEWAY_URL}/admin/policies/{pid}",
                headers=admin_headers,
            )
            data = detail.json()
            if idx == 1:
                assert data["is_active"] is True, f"Policy {idx} should be active"
            else:
                assert data["is_active"] is False, f"Policy {idx} should be inactive"

        # Verify policy 2's updated_at hasn't changed (wasn't touched).
        # We check that policy 0 was deactivated and policy 2 was never active.
        detail_2 = await http_client.get(
            f"{GATEWAY_URL}/admin/policies/{ids[2]}",
            headers=admin_headers,
        )
        d2 = detail_2.json()
        assert d2["is_active"] is False
        # updated_at should equal created_at since it was never modified
        assert d2["updated_at"] == d2["created_at"], (
            f"Policy 2 should not have been touched: updated_at={d2['updated_at']} != created_at={d2['created_at']}"
        )
    finally:
        for pid in ids:
            await _cleanup_policy(http_client, admin_headers, pid)


# ── Delete ───────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_delete_policy(http_client, admin_headers):
    """Deleting an inactive policy removes it."""
    # Save one
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/save",
        headers=admin_headers,
        json={
            "name": f"e2e-delete-{uuid.uuid4().hex[:8]}",
            "source_code": ALLCAPS_DYNAMIC_POLICY,
            "config": {},
        },
    )
    assert resp.status_code == 200
    policy_id = resp.json()["id"]

    # Delete it
    del_resp = await http_client.delete(
        f"{GATEWAY_URL}/admin/policies/{policy_id}",
        headers=admin_headers,
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["success"] is True

    # Verify gone
    get_resp = await http_client.get(
        f"{GATEWAY_URL}/admin/policies/{policy_id}",
        headers=admin_headers,
    )
    assert get_resp.status_code == 404


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_delete_active_policy_rejected(http_client, admin_headers, saved_policy):
    """Cannot delete a policy that is currently active."""
    policy_id = saved_policy["id"]

    # Activate it
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/{policy_id}/activate",
        headers=admin_headers,
    )
    assert resp.status_code == 200

    # Attempt delete
    del_resp = await http_client.delete(
        f"{GATEWAY_URL}/admin/policies/{policy_id}",
        headers=admin_headers,
    )
    assert del_resp.status_code == 400
    assert "active" in del_resp.text.lower()

    # Cleanup
    await _cleanup_policy(http_client, admin_headers, policy_id)


# ── Activated policy affects requests ────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_activated_dynamic_policy_transforms_responses(http_client, admin_headers, proxy_headers, saved_policy):
    """An activated dynamic AllCaps policy uppercases response text."""
    policy_id = saved_policy["id"]

    # Activate the dynamic policy
    resp = await http_client.post(
        f"{GATEWAY_URL}/admin/policies/{policy_id}/activate",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.5)

    # Make a non-streaming OpenAI-format request
    chat_resp = await http_client.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers=proxy_headers,
        json={
            "model": "gpt-4.1-nano",
            "messages": [{"role": "user", "content": "Say exactly: hello world"}],
            "max_tokens": 10,
            "stream": False,
        },
    )
    assert chat_resp.status_code == 200
    content = chat_resp.json()["choices"][0]["message"]["content"]
    assert content == content.upper(), f"Expected all-uppercase response, got: {content}"

    # Cleanup: restore static NoOp
    await _cleanup_policy(http_client, admin_headers, policy_id)


# ── Auth ─────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_dynamic_policy_endpoints_require_auth(http_client):
    """All dynamic policy endpoints require admin authentication."""
    endpoints = [
        ("GET", f"{GATEWAY_URL}/admin/policies/"),
        ("POST", f"{GATEWAY_URL}/admin/policies/validate"),
        ("POST", f"{GATEWAY_URL}/admin/policies/save"),
        ("GET", f"{GATEWAY_URL}/admin/policies/{uuid.uuid4()}"),
        ("POST", f"{GATEWAY_URL}/admin/policies/{uuid.uuid4()}/activate"),
        ("DELETE", f"{GATEWAY_URL}/admin/policies/{uuid.uuid4()}"),
    ]
    for method, url in endpoints:
        resp = await http_client.request(method, url)
        assert resp.status_code in (403, 401, 422), f"{method} {url} should require auth, got {resp.status_code}"
