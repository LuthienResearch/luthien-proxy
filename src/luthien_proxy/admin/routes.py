"""Admin API routes for policy management."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Literal, cast

import litellm
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field, ValidationError, field_validator

from luthien_proxy.admin.policy_discovery import discover_policies, validate_policy_config
from luthien_proxy.auth import verify_admin_token
from luthien_proxy.config import _import_policy_class
from luthien_proxy.config_registry import ConfigOverriddenError, ConfigRegistry
from luthien_proxy.credential_manager import AuthConfig, AuthMode, CredentialManager
from luthien_proxy.credentials import Credential, CredentialError, CredentialType
from luthien_proxy.dependencies import (
    Dependencies,
    get_db_pool,
    get_dependencies,
    get_emitter,
    get_policy_manager,
    get_webhook_sender,
    require_config_registry,
    require_credential_manager,
    require_inference_provider_registry,
)
from luthien_proxy.inference.registry import (
    MAX_CONFIG_JSON_BYTES,
    InferenceProviderRegistry,
    InferenceRegistryError,
    ProviderRecord,
    UnknownBackendTypeError,
)
from luthien_proxy.llm import anthropic_client_cache
from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.observability.emitter import EventEmitterProtocol
from luthien_proxy.policy_core.anthropic_execution_interface import AnthropicExecutionInterface
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_manager import (
    PolicyEnableResult,
    PolicyInfo,
    PolicyManager,
)
from luthien_proxy.settings import client_error_detail, get_settings
from luthien_proxy.types import RawHttpRequest
from luthien_proxy.usage_telemetry.config import resolve_telemetry_config
from luthien_proxy.utils import db
from luthien_proxy.utils import policy_cache as policy_cache_utils
from luthien_proxy.webhook.sender import WebhookSender

logger = logging.getLogger(__name__)

# Bounded probe for /api/admin/system-status. Short enough that a hung DB or
# Redis can't tarpit the request, long enough to absorb routine jitter.
SYSTEM_STATUS_PROBE_TIMEOUT_SECONDS = 2.0

router = APIRouter(prefix="/api/admin", tags=["admin"])


class PolicySetRequest(BaseModel):
    """Request to set the active policy."""

    policy_class_ref: str = Field(..., description="Full module path to policy class")
    config: dict[str, Any] = Field(default_factory=dict, description="Configuration for the policy")
    enabled_by: str = Field(default="api", description="Identifier of who enabled the policy")


class PolicyEnableResponse(BaseModel):
    """Response from enabling a policy."""

    success: bool
    message: str | None = None
    policy: str | None = None
    restart_duration_ms: int | None = None
    error: str | None = None
    troubleshooting: list[str] | None = None
    validation_errors: list[dict] | None = None


class PolicyCurrentResponse(BaseModel):
    """Response with current policy information."""

    policy: str
    class_ref: str
    enabled_at: str | None
    enabled_by: str | None
    config: dict[str, Any]


class PolicyClassInfo(BaseModel):
    """Information about an available policy class."""

    name: str = Field(..., description="Policy class name (e.g., 'NoOpPolicy')")
    class_ref: str = Field(..., description="Full module path to policy class")
    description: str = Field(..., description="Description of what the policy does")
    config_schema: dict[str, Any] = Field(default_factory=dict, description="Schema for config parameters")
    example_config: dict[str, Any] = Field(default_factory=dict, description="Example configuration")
    # UI catalog metadata. No runtime effect; consumed by /policy-config catalog UI.
    category: str = Field(default="advanced", description="UI catalog category for top-level grouping")
    display_name: str = Field(default="", description="Friendly display name (e.g., 'De-Slop')")
    short_description: str = Field(default="", description="One-liner for the catalog card")
    catalog_badges: list[str] = Field(
        default_factory=list,
        description="UI tag chips next to the display name (e.g., 'Blocks', 'Judge')",
    )
    ui_policy_preview: str = Field(
        default="",
        description=(
            "UI hint shown on the catalog card. PREVIEW ONLY — production output may "
            "differ for LLM-judge or templated runtime alerts."
        ),
    )


class PolicyListResponse(BaseModel):
    """Response with list of available policy classes."""

    policies: list[PolicyClassInfo]


class ChatRequest(BaseModel):
    """Request for testing chat through the proxy."""

    model: str = Field(..., description="Model to use (e.g., 'claude-haiku-4-5', 'claude-sonnet-4-5')")
    message: str = Field(..., description="Message to send")
    stream: bool = Field(default=False, description="Whether to stream the response")
    use_mock: bool = Field(
        default=False,
        description="Use a mock LLM response so no upstream API key is required. "
        "The policy pipeline still runs on both the request and the mock response. "
        "Set to True to skip the real LLM call (useful when no server-side LLM credentials are configured).",
    )
    api_key: str | None = Field(
        default=None,
        description="Optional Anthropic API key to use for this test request. "
        "Overrides the server's configured Anthropic credential. The test endpoint "
        "calls Anthropic directly (not through the gateway HTTP boundary), so this "
        "key is sent to Anthropic, not used to authenticate against the proxy.",
    )


class ChatResponse(BaseModel):
    """Response from the admin policy-test endpoint.

    The endpoint runs two steps:
      1. Call Anthropic directly with the original request → ``before_content``.
      2. Run the active policy's request/response hooks against that exchange,
         re-calling Anthropic if the request hook transforms the request → ``content``.

    Operators use the diff between ``before_content`` and ``content`` to verify
    a policy actually does what they think before activating it on real traffic.

    Caveat — model jitter: when the request hook rewrites the request and
    triggers a second LLM call, ``before_content`` and ``content`` reflect
    *two independent* LLM samples. Differences can come from sampling
    variance, not just from the policy. For a strict "policy effect only"
    diff, prefer policies that transform responses rather than requests, or
    set ``temperature=0`` upstream.
    """

    success: bool
    content: str | None = Field(
        default=None,
        description="Post-policy content (After). Reflects the active policy's full effect.",
    )
    before_content: str | None = Field(
        default=None,
        description="Raw LLM content (Before) — what Anthropic returned for the original request "
        "with no policy in the way. None when the LLM call failed or in mock mode. "
        "When the active policy rewrites the request and a second LLM call is "
        "issued for After, the Before/After diff includes sampling variance from "
        "two independent draws, not just the policy's effect.",
    )
    error: str | None = None
    model: str | None = None
    usage: dict[str, Any] | None = Field(
        default=None,
        description="Usage stats from the After-side LLM call. When the request was not "
        "transformed (single-call optimization), this equals ``before_usage``.",
    )
    before_usage: dict[str, Any] | None = Field(
        default=None,
        description="Usage stats from the Before-side LLM call. Surfaced so operators can "
        "see total cost when the policy triggers a second LLM call (Before + After). "
        "When no second call is issued, equals ``usage``.",
    )


class AuthConfigResponse(BaseModel):
    """Response with current auth configuration."""

    auth_mode: str
    validate_credentials: bool
    valid_cache_ttl_seconds: int
    invalid_cache_ttl_seconds: int
    updated_at: str | None = None
    updated_by: str | None = None


class BillingStatusResponse(BaseModel):
    """Response with billing-mode signals for the admin UI badge.

    These fields previously rode along on the unauthenticated /health
    response, which leaked auth-mode and recent-activity fingerprinting
    information. The admin UI now fetches this from an authenticated
    endpoint instead.
    """

    auth_mode: str | None
    last_credential_type: str | None
    last_credential_at: float | None


class ComponentCheck(BaseModel):
    """Per-component probe result for the system-status endpoint."""

    status: Literal["ok", "error", "not_configured"]
    latency_ms: float | None = None
    error: str | None = None


class SystemStatusResponse(BaseModel):
    """Rich per-component diagnostics for operators and monitoring tools.

    Behind admin auth (not on unauthenticated /health) so the latency timing
    and dependency-topology signals can't be used to fingerprint or probe the
    gateway, and so the DB/Redis probes can't be used as an unauthenticated
    DoS amplifier against the connection pool.
    """

    status: Literal["healthy", "degraded", "unhealthy"]
    checks: dict[str, ComponentCheck]


class AuthConfigUpdateRequest(BaseModel):
    """Request to update auth configuration."""

    auth_mode: str | None = Field(default=None, description="Auth mode: client_key, passthrough, or both")
    validate_credentials: bool | None = Field(default=None)
    valid_cache_ttl_seconds: int | None = Field(default=None, gt=0)
    invalid_cache_ttl_seconds: int | None = Field(default=None, gt=0)


class CachedCredentialResponse(BaseModel):
    """A cached credential entry."""

    key_hash: str
    valid: bool
    validated_at: float
    last_used_at: float


class CachedCredentialsListResponse(BaseModel):
    """Response with list of cached credentials."""

    credentials: list[CachedCredentialResponse]
    count: int


def get_available_models() -> list[str]:
    """Get available Anthropic models for testing.

    Returns a list of Claude models available via litellm.
    """
    anthropic_models = [m for m in litellm.anthropic_models if "claude" in m.lower()]
    return sorted(anthropic_models, reverse=True)


@router.get("/policy/current", response_model=PolicyCurrentResponse)
async def get_current_policy(
    _: str = Depends(verify_admin_token),
    manager: PolicyManager = Depends(get_policy_manager),
):
    """Get currently active policy with metadata.

    Returns information about the currently active policy including
    its configuration and when it was enabled.

    Requires admin authentication.
    """
    try:
        policy_info: PolicyInfo = await manager.get_current_policy()
        return PolicyCurrentResponse(
            policy=policy_info.policy,
            class_ref=policy_info.class_ref,
            enabled_at=policy_info.enabled_at,
            enabled_by=policy_info.enabled_by,
            config=policy_info.config,
        )
    except Exception as e:
        logger.error(f"Failed to get current policy: {repr(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=client_error_detail(f"Failed to get current policy: {e}"))


@router.post("/policy/set", response_model=PolicyEnableResponse)
async def set_policy(
    body: PolicySetRequest,
    _: str = Depends(verify_admin_token),
    manager: PolicyManager = Depends(get_policy_manager),
):
    """Set the active policy.

    This is the primary endpoint for changing the active policy.
    The policy is validated, activated in memory, and persisted to the database.

    Requires admin authentication.
    """
    try:
        # Import policy class and validate config before enabling
        policy_class = _import_policy_class(body.policy_class_ref)
        validated_config = validate_policy_config(policy_class, body.config or {})

        result: PolicyEnableResult = await manager.enable_policy(
            policy_class_ref=body.policy_class_ref,
            config=validated_config,
            enabled_by=body.enabled_by,
        )

        if not result.success:
            return PolicyEnableResponse(
                success=False,
                message=f"Failed to set policy: {result.error}",
                error=result.error,
                troubleshooting=result.troubleshooting,
            )

        return PolicyEnableResponse(
            success=True,
            message=f"Policy set to {body.policy_class_ref}",
            policy=result.policy,
            restart_duration_ms=result.restart_duration_ms,
        )
    except ValidationError as e:
        return PolicyEnableResponse(
            success=False,
            error="Validation error",
            troubleshooting=[f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()],
            validation_errors=[dict(err) for err in e.errors()],
        )
    except ValueError as e:
        logger.warning(f"Policy validation error: {repr(e)}")
        return PolicyEnableResponse(
            success=False,
            error="Validation error",
            troubleshooting=[client_error_detail(str(e), "Check the policy configuration values and try again.")],
        )
    except (ImportError, AttributeError, TypeError) as e:
        logger.warning(f"Policy load error: {repr(e)}")
        return PolicyEnableResponse(
            success=False,
            error=client_error_detail(str(e), "Failed to load policy class."),
            troubleshooting=[
                "Check that the policy class reference is correct",
                "Verify the policy module exists and is importable",
                "Example format: 'luthien_proxy.policies.all_caps_policy:AllCapsPolicy'",
            ],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set policy: {repr(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=client_error_detail(str(e)))


@router.get("/policy/list", response_model=PolicyListResponse)
async def list_available_policies(
    _: str = Depends(verify_admin_token),
):
    """List available policy classes with metadata.

    Returns information about all available policy classes including:
    - Policy name and class reference
    - Description of what the policy does
    - Configuration schema (parameter names, types, defaults)
    - Example configuration

    This endpoint helps users discover what policies are available and
    how to configure them.

    Requires admin authentication.
    """
    discovered = discover_policies()
    policies = [
        PolicyClassInfo(
            name=p["name"],
            class_ref=p["class_ref"],
            description=p["description"],
            config_schema=p["config_schema"],
            example_config=p["example_config"],
            category=p.get("category", "advanced"),
            display_name=p.get("display_name", ""),
            short_description=p.get("short_description", ""),
            catalog_badges=p.get("catalog_badges", []),
            ui_policy_preview=p.get("ui_policy_preview", ""),
        )
        for p in discovered
    ]
    return PolicyListResponse(policies=policies)


@router.get("/models")
async def list_models(
    _: str = Depends(verify_admin_token),
):
    """List available models for testing.

    Returns a list of Anthropic Claude models available via litellm.
    Requires admin authentication.
    """
    return {"models": get_available_models()}


def _coerce_usage(response: AnthropicResponse | None) -> dict[str, Any] | None:
    """Convert AnthropicUsage TypedDict (or absent usage) to a plain dict.

    Pyright treats TypedDicts as a distinct type from ``dict[str, Any]``;
    the ChatResponse field is the latter, so coerce explicitly.
    """
    if response is None:
        return None
    usage = response.get("usage")
    if usage is None:
        return None
    return dict(usage)


def _extract_text_content(response: AnthropicResponse | None) -> str | None:
    """Concatenate text-block content from an Anthropic response.

    Tool-use, thinking, and other non-text blocks are intentionally elided —
    text concatenation is a useful approximation for the operator-facing
    Before/After preview, even if the underlying response carries richer
    structure. The full response object is not surfaced to the UI.
    """
    if response is None:
        return None
    parts: list[str] = []
    for block in response.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    if not parts:
        return None
    return "".join(parts)


def _snapshot_request(request: AnthropicRequest) -> AnthropicRequest:
    """Deep-copy a request so we can compare pre- and post-hook state.

    A policy whose ``on_anthropic_request`` mutates the input dict in place
    and returns the same reference will defeat any post-hook equality check
    that reads the dict on both sides — both sides see post-mutation state,
    look equal, and the optimizer would wrongly skip the second LLM call.
    Snapshot before invoking the hook to keep the comparison honest.
    """
    return cast(AnthropicRequest, copy.deepcopy(request))


async def _resolve_test_anthropic_client(
    body_api_key: str | None,
    server_client: AnthropicClient | None,
) -> tuple[AnthropicClient | None, str | None]:
    """Resolve the AnthropicClient to use for a test-chat call.

    Precedence:
      1. Caller-supplied api_key (forwards directly to Anthropic, cached).
      2. Server's configured upstream client (set via ANTHROPIC_API_KEY).
    Returns (client, error_message). On failure, client is None.
    """
    if body_api_key is not None and body_api_key.strip():
        try:
            client = await anthropic_client_cache.get_client(
                body_api_key.strip(),
                auth_type="api_key",
            )
            return client, None
        except Exception as exc:
            logger.error(f"Failed to build AnthropicClient from supplied api_key: {repr(exc)}")
            return None, "Failed to initialize Anthropic client with supplied api_key"

    if server_client is not None:
        return server_client, None

    return None, (
        "No Anthropic API key available — set ANTHROPIC_API_KEY on the server or supply api_key in the request body"
    )


def _build_test_user_credential(body_api_key: str | None) -> Credential | None:
    """Construct the user_credential a test-path policy should observe.

    Mirrors the gateway's credential-shape semantics:
      - body.api_key supplied → passthrough-style ``Credential(API_KEY)`` so
        policies that key off ``ctx.user_credential`` see exactly what they
        would for a passthrough request.
      - Otherwise → None, matching the gateway's "client-key match" branch
        where the inbound credential authenticated the request but the
        upstream call uses the server's ANTHROPIC_API_KEY (no per-user
        credential to forward). Policies that strictly require a user
        credential will surface the same error they would for a real
        client-key request — that's the realistic preview, not a bug.
    """
    if body_api_key is not None and body_api_key.strip():
        return Credential(
            value=body_api_key.strip(),
            credential_type=CredentialType.API_KEY,
            platform="anthropic",
        )
    return None


def _build_test_raw_http_request(
    fastapi_request: Request,
    original_request: AnthropicRequest,
) -> RawHttpRequest:
    """Build a RawHttpRequest that reflects the admin test invocation.

    ``RawHttpRequest`` exists so policies can recover headers/body that the
    typed ``AnthropicRequest`` doesn't carry. For the admin test path we
    expose the inbound headers (e.g. ``anthropic-beta``, ``x-session-id``)
    from the admin caller and the synthetic Anthropic body — the exact
    surface a policy would see if this same request had landed on
    ``/v1/messages``.

    Note: the inbound path is the admin URL, not ``/v1/messages``. Policies
    that gate behavior on the request path (uncommon) will see
    ``/api/admin/test/chat`` and can reasonably treat that as a test
    invocation.
    """
    headers = {k.lower(): v for k, v in fastapi_request.headers.items()}
    # Same identity as production: pipeline/anthropic_processor.py constructs
    # RawHttpRequest with the parsed JSON body and uses that same dict as the
    # AnthropicRequest, so ``raw_http_request.body is anthropic_request`` in
    # production. Mirror that here by aliasing the request directly — without
    # this, a policy that mutates ``original_request`` in ``on_anthropic_request``
    # and then reads ``ctx.raw_http_request.body`` would see different state in
    # the test path than in production, and the Before/After preview would lie
    # about what production would do.
    body = cast(dict[str, Any], original_request)
    return RawHttpRequest(
        body=body,
        headers=headers,
        method=fastapi_request.method,
        path=fastapi_request.url.path,
    )


def _build_test_policy_context(
    *,
    transaction_id: str,
    original_request: AnthropicRequest,
    fastapi_request: Request,
    emitter: EventEmitterProtocol,
    credential_manager: CredentialManager,
    db_pool: db.DatabasePool | None,
    body_api_key: str | None,
) -> PolicyContext:
    """Build a full PolicyContext matching the one the gateway pipeline creates.

    The directive: a test-path policy must see the same context shape it
    would for real ``/v1/messages`` traffic. That means the same emitter
    (test-path events DO appear in the activity monitor — by design, see
    the changelog), the same credential manager, the same policy cache
    factory, and a credential whose type/value matches what a passthrough
    request would carry. The session_id is a per-test synthetic marker so
    the activity monitor groups *this* Before/After run (a single
    ``send_chat`` invocation) as one logical session and operators can
    identify test traffic at a glance. Consecutive admin-test invocations
    are independent sessions — each call generates a fresh
    ``admin-test-session-{8-hex}`` id.
    """
    raw_http_request = _build_test_raw_http_request(fastapi_request, original_request)
    user_credential = _build_test_user_credential(body_api_key)
    policy_cache_factory = policy_cache_utils.build_factory(db_pool)
    # Synthetic but stable-shaped session id; prefix marks the run as admin
    # test traffic for anyone reading the activity stream. Operators who
    # filter for production traffic can drop ``admin-test-*`` sessions.
    session_id = f"admin-test-session-{uuid.uuid4().hex[:8]}"

    return PolicyContext(
        transaction_id=transaction_id,
        request=None,  # No OpenAI-format request — native Anthropic path.
        emitter=emitter,
        raw_http_request=raw_http_request,
        session_id=session_id,
        user_credential=user_credential,
        credential_manager=credential_manager,
        policy_cache_factory=policy_cache_factory,
    )


@router.post("/test/chat", response_model=ChatResponse)
async def send_chat(
    body: ChatRequest,
    fastapi_request: Request,
    _: str = Depends(verify_admin_token),
    deps: Dependencies = Depends(get_dependencies),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
    credential_manager: CredentialManager = Depends(require_credential_manager),
    emitter: EventEmitterProtocol = Depends(get_emitter),
):
    """Send a test message and return Before/After previews of the active policy.

    This endpoint orchestrates two steps directly, never crossing the
    ``/v1/messages`` HTTP boundary:

      1. Call Anthropic with the operator's original request → ``before_content``
         (what the LLM would have said with no policy in the way).
      2. Run the active policy's ``on_anthropic_request`` and ``on_anthropic_response``
         hooks against a full ``PolicyContext`` (same emitter, credential
         manager, and policy cache the gateway pipeline uses) and re-call
         Anthropic if the request was transformed → ``content`` (what the
         policy turned the LLM's response into).

    Architectural note: policies decide what happens to a request — clients
    (including this admin test path) do not. The orchestration runs the policy
    hooks programmatically, so the gateway's request/response pipeline is never
    involved and no client-facing protocol opt-in is required. The
    PolicyContext is built with the same dependencies the gateway hands to
    its pipeline so judge-style policies (LLM judges, ToolCallJudgePolicy,
    DogfoodSafety, the Block presets) can run against test traffic exactly
    as they would against real traffic.

    Observability note: the test-path PolicyContext shares the production
    emitter. Test-run policy events DO appear in the activity monitor and
    are recorded for observability — the session_id is prefixed
    ``admin-test-session-`` so operators can identify or filter test
    traffic. (See changelog.)

    Streaming-only policies (those that only implement ``on_anthropic_stream_event``)
    will appear as no-ops in the After view. The non-streaming hooks are the
    source of truth for this endpoint by design — faking a stream would be
    misleading. Tool-use and thinking blocks are elided from the text preview.

    Caveat — model jitter: when ``on_anthropic_request`` rewrites the
    request and triggers a second LLM call, ``before_content`` and
    ``content`` are *two independent* LLM samples. The diff includes
    sampling variance, not just the policy's effect. Operators should
    prefer ``temperature=0`` for clean policy-effect previews of
    request-transforming policies.

    Requires admin authentication.
    """
    # Mock mode: skip both the LLM call and the policy. This is the lightest
    # operator-facing smoke test (no API credits, no policy needed). Before
    # and After are both the echoed message — the diff is intentionally empty
    # so the operator sees that mock mode is a no-op end-to-end.
    if body.use_mock:
        return ChatResponse(
            success=True,
            content=body.message,
            before_content=body.message,
            model=body.model,
        )

    # Resolve the upstream Anthropic client first — fast-fail if no creds.
    client, key_error = await _resolve_test_anthropic_client(body.api_key, deps.anthropic_client)
    if client is None:
        return ChatResponse(success=False, error=key_error, model=body.model)

    # Resolve the active policy. If the active policy doesn't implement the
    # Anthropic hook surface this raises HTTPException(500) — same behavior as
    # the gateway path.
    try:
        policy: AnthropicExecutionInterface = deps.get_anthropic_policy()
    except HTTPException:
        # Let FastAPI handle HTTPException (preserves status code); fall through
        # to the catch-all only for unexpected errors. Don't simplify this away.
        raise
    except Exception as e:
        logger.error(f"Failed to resolve active policy: {repr(e)}", exc_info=True)
        return ChatResponse(
            success=False,
            error=client_error_detail(str(e), "Failed to resolve active policy"),
            model=body.model,
        )

    # Build the original Anthropic request. Kept minimal on purpose — the test
    # endpoint is a preview tool, not a full conversation harness.
    original_request: AnthropicRequest = cast(
        AnthropicRequest,
        {
            "model": body.model,
            "messages": [{"role": "user", "content": body.message}],
            "max_tokens": 1024,
        },
    )

    transaction_id = f"admin-test-{uuid.uuid4().hex[:12]}"
    ctx = _build_test_policy_context(
        transaction_id=transaction_id,
        original_request=original_request,
        fastapi_request=fastapi_request,
        emitter=emitter,
        credential_manager=credential_manager,
        db_pool=db_pool,
        body_api_key=body.api_key,
    )

    # Mirror the gateway's anthropic-beta header forwarding so beta features
    # (prompt caching, etc.) behave identically in this preview and in
    # production. See pipeline/anthropic_processor.py — same line.
    forwarded_headers: dict[str, str] | None = None
    if beta := fastapi_request.headers.get("anthropic-beta"):
        forwarded_headers = {"anthropic-beta": beta}

    # Step 1: Before — call Anthropic with the unmodified original request.
    try:
        before_response = await client.complete(original_request, extra_headers=forwarded_headers)
    except Exception as e:
        logger.error(f"Test chat: LLM call (before) failed: {repr(e)}", exc_info=True)
        return ChatResponse(
            success=False,
            error=client_error_detail(str(e), "Anthropic API call failed"),
            model=body.model,
        )

    # Capture before_content/before_usage from the pre-hook response so that
    # response-hook mutations of ``before_response`` (when ``upstream_for_after
    # = before_response`` in the request-hook-passthrough path) don't poison
    # the Before view. Don't reorder these below the response hook.
    before_content = _extract_text_content(before_response)
    before_usage = _coerce_usage(before_response)

    # Step 2: After — run policy hooks. on_anthropic_request may rewrite the
    # request; if it did, we re-call Anthropic with the transformed request so
    # the After preview reflects the realistic full-pipeline outcome. If the
    # request hook is a passthrough, reuse the Before response (one LLM call).
    #
    # The pre-hook snapshot is critical: a policy that mutates the input dict
    # in place and returns the same reference would defeat any post-hook
    # equality check that reads the live dict on both sides. Comparison is
    # against the snapshot taken BEFORE the hook ran.
    pre_hook_snapshot = _snapshot_request(original_request)
    try:
        transformed_request = await policy.on_anthropic_request(original_request, ctx)
    except Exception as e:
        logger.error(f"Test chat: policy.on_anthropic_request failed: {repr(e)}", exc_info=True)
        return ChatResponse(
            success=False,
            before_content=before_content,
            before_usage=before_usage,
            error=client_error_detail(str(e), "Policy request hook failed"),
            model=body.model,
            usage=before_usage,
        )

    if transformed_request == pre_hook_snapshot:
        upstream_for_after = before_response
    else:
        try:
            upstream_for_after = await client.complete(transformed_request, extra_headers=forwarded_headers)
        except Exception as e:
            logger.error(f"Test chat: LLM call (after, transformed request) failed: {repr(e)}", exc_info=True)
            return ChatResponse(
                success=False,
                before_content=before_content,
                before_usage=before_usage,
                error=client_error_detail(str(e), "Anthropic API call (post-request-hook) failed"),
                model=body.model,
                usage=before_usage,
            )

    try:
        after_response = await policy.on_anthropic_response(upstream_for_after, ctx)
    except Exception as e:
        logger.error(f"Test chat: policy.on_anthropic_response failed: {repr(e)}", exc_info=True)
        return ChatResponse(
            success=False,
            before_content=before_content,
            before_usage=before_usage,
            error=client_error_detail(str(e), "Policy response hook failed"),
            model=body.model,
            usage=before_usage,
        )

    after_content = _extract_text_content(after_response)
    usage = _coerce_usage(after_response)

    return ChatResponse(
        success=True,
        content=after_content,
        before_content=before_content,
        before_usage=before_usage,
        model=body.model,
        usage=usage,
    )


def _config_to_response(config: AuthConfig) -> AuthConfigResponse:
    return AuthConfigResponse(
        auth_mode=config.auth_mode.value,
        validate_credentials=config.validate_credentials,
        valid_cache_ttl_seconds=config.valid_cache_ttl_seconds,
        invalid_cache_ttl_seconds=config.invalid_cache_ttl_seconds,
        updated_at=config.updated_at,
        updated_by=config.updated_by,
    )


@router.get("/auth/config", response_model=AuthConfigResponse)
async def get_auth_config(
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Get current authentication configuration."""
    return _config_to_response(credential_manager.config)


@router.get("/billing-status", response_model=BillingStatusResponse)
async def get_billing_status(
    _: str = Depends(verify_admin_token),
    deps: Dependencies = Depends(get_dependencies),
):
    """Return billing-mode signals (auth_mode, last credential type/timestamp).

    Used by the admin UI nav bar to render the API-key-billing warning badge.
    Behind admin auth so the values are not exposed to unauthenticated probes
    (a probe attacker could otherwise fingerprint the gateway's auth mode and
    recent activity via /health).
    """
    auth_mode = deps.credential_manager.config.auth_mode.value if deps.credential_manager else None
    last_type = deps.last_credential_info.get("type") if deps.last_credential_info else None
    last_at = deps.last_credential_info.get("timestamp") if deps.last_credential_info else None
    return BillingStatusResponse(
        auth_mode=auth_mode,
        last_credential_type=last_type,
        last_credential_at=last_at,
    )


async def _probe_db(deps: Dependencies) -> ComponentCheck:
    """Probe the database with a bounded SELECT 1."""
    db_pool = deps.db_pool
    if db_pool is None:
        return ComponentCheck(status="not_configured")

    async def _run() -> None:
        async with db_pool.connection() as conn:
            await conn.fetchval("SELECT 1")

    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(_run(), timeout=SYSTEM_STATUS_PROBE_TIMEOUT_SECONDS)
        return ComponentCheck(status="ok", latency_ms=round((time.perf_counter() - t0) * 1000, 1))
    except asyncio.TimeoutError:
        logger.warning("system-status: DB probe timed out after %.1fs", SYSTEM_STATUS_PROBE_TIMEOUT_SECONDS)
        return ComponentCheck(
            status="error", latency_ms=round((time.perf_counter() - t0) * 1000, 1), error="database check timed out"
        )
    except Exception:
        logger.warning("system-status: DB probe failed", exc_info=True)
        return ComponentCheck(
            status="error", latency_ms=round((time.perf_counter() - t0) * 1000, 1), error="database check failed"
        )


async def _probe_redis(deps: Dependencies) -> ComponentCheck:
    """Probe Redis with a bounded ping."""
    redis_client = deps.redis_client
    if redis_client is None:
        return ComponentCheck(status="not_configured")
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(redis_client.ping(), timeout=SYSTEM_STATUS_PROBE_TIMEOUT_SECONDS)
        return ComponentCheck(status="ok", latency_ms=round((time.perf_counter() - t0) * 1000, 1))
    except asyncio.TimeoutError:
        logger.warning("system-status: Redis probe timed out after %.1fs", SYSTEM_STATUS_PROBE_TIMEOUT_SECONDS)
        return ComponentCheck(
            status="error", latency_ms=round((time.perf_counter() - t0) * 1000, 1), error="redis check timed out"
        )
    except Exception:
        logger.warning("system-status: Redis probe failed", exc_info=True)
        return ComponentCheck(
            status="error", latency_ms=round((time.perf_counter() - t0) * 1000, 1), error="redis check failed"
        )


@router.get("/system-status", response_model=SystemStatusResponse)
async def get_system_status(
    _: str = Depends(verify_admin_token),
    deps: Dependencies = Depends(get_dependencies),
) -> SystemStatusResponse:
    """Rich per-component health diagnostics for operators and monitoring.

    Probes DB and Redis in parallel (each bounded by
    SYSTEM_STATUS_PROBE_TIMEOUT_SECONDS) and reports per-component status and
    latency so a degraded gateway (e.g. Redis down) can be distinguished from
    an unhealthy one (DB down).

    This is deliberately NOT on /health: /health is a dependency-free liveness
    probe for container/k8s restart logic, and /ready handles traffic-draining
    readiness. This endpoint is for a monitoring system that parses the body,
    and is behind admin auth so its timing and topology signals aren't exposed
    to unauthenticated probes.
    """
    db_check, redis_check = await asyncio.gather(_probe_db(deps), _probe_redis(deps))

    # DB is required: error OR not_configured means the gateway can't serve
    # traffic (matches /ready's 503 when db_pool is None). Redis is optional —
    # not_configured (SQLite/local mode) stays healthy; only a Redis *error*
    # degrades.
    if db_check.status in ("error", "not_configured"):
        overall = "unhealthy"
    elif redis_check.status == "error":
        overall = "degraded"
    else:
        overall = "healthy"

    return SystemStatusResponse(status=overall, checks={"db": db_check, "redis": redis_check})


@router.post("/auth/config", response_model=AuthConfigResponse)
async def update_auth_config(
    body: AuthConfigUpdateRequest,
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Update authentication configuration."""
    if body.auth_mode is not None:
        try:
            AuthMode(body.auth_mode)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid auth_mode: {body.auth_mode}. Must be one of: client_key, passthrough, both",
            )

    config = await credential_manager.update_config(
        auth_mode=body.auth_mode,
        validate_credentials=body.validate_credentials,
        valid_cache_ttl_seconds=body.valid_cache_ttl_seconds,
        invalid_cache_ttl_seconds=body.invalid_cache_ttl_seconds,
        updated_by="admin-api",
    )
    return _config_to_response(config)


@router.get("/auth/credentials", response_model=CachedCredentialsListResponse)
async def list_cached_credentials(
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """List all cached credentials (hashes and metadata only)."""
    cached = await credential_manager.list_cached()
    credentials = [
        CachedCredentialResponse(
            key_hash=c.key_hash,
            valid=c.valid,
            validated_at=c.validated_at,
            last_used_at=c.last_used_at,
        )
        for c in cached
    ]
    return CachedCredentialsListResponse(credentials=credentials, count=len(credentials))


@router.delete("/auth/credentials/{key_hash}")
async def invalidate_credential(
    key_hash: str,
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Invalidate a single cached credential by its hash."""
    found = await credential_manager.invalidate_credential(key_hash)
    if not found:
        raise HTTPException(status_code=404, detail="Credential not found in cache")
    return {"success": True, "message": "Credential invalidated"}


@router.delete("/auth/credentials")
async def invalidate_all_credentials(
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Invalidate all cached credentials."""
    count = await credential_manager.invalidate_all()
    return {"success": True, "count": count, "message": f"Invalidated {count} cached credentials"}


# === Server Credentials ===


class ServerCredentialRequest(BaseModel):
    """Request to create/update a server credential."""

    name: str = Field(
        ...,
        description="Unique name for the credential (e.g. 'judge-api-key')",
        pattern=r"^[a-zA-Z0-9_-]{1,128}$",
    )
    value: str = Field(..., min_length=1, description="The credential value (API key or OAuth token)")
    credential_type: str = Field(default="api_key", description="'api_key' or 'auth_token'")
    platform: str = Field(default="anthropic", description="Provider platform")
    platform_url: str | None = Field(default=None, description="Custom base URL")


@router.post("/credentials")
async def put_server_credential(
    body: ServerCredentialRequest,
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Create or update a server credential."""
    try:
        cred_type = CredentialType(body.credential_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid credential_type: {body.credential_type}. Must be 'api_key' or 'auth_token'",
        )

    credential = Credential(
        value=body.value,
        credential_type=cred_type,
        platform=body.platform,
        platform_url=body.platform_url,
    )
    try:
        await credential_manager.put_server_credential(body.name, credential)
    except CredentialError as e:
        logger.error("Server credential put failed: %r", e)
        raise HTTPException(status_code=503, detail="Server credential operation failed")
    return {"success": True, "name": body.name}


@router.get("/credentials")
async def list_server_credentials(
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """List server credential names (no values exposed)."""
    names = await credential_manager.list_server_credentials()
    return {"credentials": names, "count": len(names)}


@router.delete("/credentials/{name}")
async def delete_server_credential(
    name: str = Path(pattern=r"^[a-zA-Z0-9_-]{1,128}$"),
    _: str = Depends(verify_admin_token),
    credential_manager: CredentialManager = Depends(require_credential_manager),
):
    """Delete a server credential."""
    try:
        deleted = await credential_manager.delete_server_credential(name)
    except CredentialError as e:
        logger.error("Server credential delete failed: %r", e)
        raise HTTPException(status_code=503, detail="Server credential operation failed")
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Server credential '{name}' not found")
    return {"success": True, "name": name}


# === Inference Providers ===


class InferenceProviderRequest(BaseModel):
    """Request to create or update a named inference provider."""

    name: str = Field(
        ...,
        description="Unique provider name (e.g. 'judge-subscription').",
        pattern=r"^[a-zA-Z0-9_-]{1,128}$",
    )
    backend_type: str = Field(
        ...,
        description="Backend implementation key. Currently 'claude_code' or 'direct_api'.",
    )
    credential_name: str | None = Field(
        default=None,
        description="Optional server_credentials.name to authenticate this provider. "
        "Soft reference — credential deletion surfaces at get() time.",
        pattern=r"^[a-zA-Z0-9_-]{1,128}$",
    )
    default_model: str = Field(
        ...,
        min_length=1,
        description="Model name passed to the backend by default (e.g. 'claude-sonnet-4-6').",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Backend-specific config (e.g. timeout_seconds, api_base). "
        "Validated per-backend at provider-construction time.",
    )

    @field_validator("config")
    @classmethod
    def _check_config_size(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Reject config blobs that exceed the registry's byte ceiling.

        Enforced at the request boundary so the admin UI gets a clear
        422 rather than a DB-level payload failure.
        """
        encoded = json.dumps(config, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_CONFIG_JSON_BYTES:
            raise ValueError(f"config JSON is {len(encoded)} bytes, exceeds maximum of {MAX_CONFIG_JSON_BYTES} bytes")
        return config


class InferenceProviderResponse(BaseModel):
    """Single provider record in API responses."""

    name: str
    backend_type: str
    credential_name: str | None
    default_model: str
    config: dict[str, Any]
    created_at: str | None
    updated_at: str | None
    known_backend: bool = Field(
        ...,
        description="True if this provider's backend_type is registered in the running gateway. "
        "False means the row was written against a backend that's been removed or isn't "
        "deployed yet; the UI should mark it and disable in-place edit.",
    )


class InferenceProviderListResponse(BaseModel):
    """List response for inference providers."""

    providers: list[InferenceProviderResponse]
    count: int
    known_backend_types: list[str] = Field(
        default_factory=list,
        description="All backend_type keys the running gateway can construct. Lets the UI "
        "render the create/edit dropdown and flag unknown backends.",
    )


def _record_to_response(record: ProviderRecord, known: set[str]) -> InferenceProviderResponse:
    """Shape a registry record for JSON serialization."""
    return InferenceProviderResponse(
        name=record.name,
        backend_type=record.backend_type,
        credential_name=record.credential_name,
        default_model=record.default_model,
        config=record.config,
        created_at=record.created_at,
        updated_at=record.updated_at,
        known_backend=record.backend_type in known,
    )


@router.post("/inference-providers")
async def put_inference_provider(
    body: InferenceProviderRequest,
    _: str = Depends(verify_admin_token),
    registry: InferenceProviderRegistry = Depends(require_inference_provider_registry),
):
    """Create or update a named inference provider."""
    record = ProviderRecord(
        name=body.name,
        backend_type=body.backend_type,
        credential_name=body.credential_name,
        default_model=body.default_model,
        config=body.config,
    )
    try:
        await registry.put(record)
    except UnknownBackendTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except InferenceRegistryError as exc:
        logger.error("Inference provider put failed: %r", exc)
        raise HTTPException(status_code=503, detail="Inference provider operation failed")
    return {"success": True, "name": body.name}


@router.get("/inference-providers", response_model=InferenceProviderListResponse)
async def list_inference_providers(
    _: str = Depends(verify_admin_token),
    registry: InferenceProviderRegistry = Depends(require_inference_provider_registry),
):
    """List configured inference providers."""
    records = await registry.list()
    known = set(registry.known_backend_types())
    responses = [_record_to_response(r, known) for r in records]
    return InferenceProviderListResponse(
        providers=responses,
        count=len(responses),
        known_backend_types=sorted(known),
    )


@router.delete("/inference-providers/{name}")
async def delete_inference_provider(
    name: str = Path(pattern=r"^[a-zA-Z0-9_-]{1,128}$"),
    _: str = Depends(verify_admin_token),
    registry: InferenceProviderRegistry = Depends(require_inference_provider_registry),
):
    """Delete a named inference provider."""
    try:
        deleted = await registry.delete(name)
    except InferenceRegistryError as exc:
        logger.error("Inference provider delete failed: %r", exc)
        raise HTTPException(status_code=503, detail="Inference provider operation failed")
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Inference provider '{name}' not found")
    return {"success": True, "name": name}


# === Telemetry ===


class TelemetryConfigResponse(BaseModel):
    """Response with current telemetry configuration."""

    enabled: bool
    deployment_id: str
    env_override: bool
    user_configured: bool


class TelemetryConfigUpdateRequest(BaseModel):
    """Request to update telemetry enabled state."""

    enabled: bool


@router.get("/telemetry")
async def get_telemetry_config(
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """Get current telemetry configuration."""
    settings = get_settings()
    config = await resolve_telemetry_config(db_pool=db_pool, env_value=settings.usage_telemetry)
    return TelemetryConfigResponse(
        enabled=config.enabled,
        deployment_id=config.deployment_id,
        env_override=settings.usage_telemetry is not None,
        user_configured=config.user_configured,
    )


@router.put("/telemetry")
async def update_telemetry_config(
    body: TelemetryConfigUpdateRequest,
    _: str = Depends(verify_admin_token),
    db_pool: db.DatabasePool | None = Depends(get_db_pool),
):
    """Update telemetry enabled state (stored in DB)."""
    settings = get_settings()
    if settings.usage_telemetry is not None:
        raise HTTPException(
            status_code=409,
            detail="USAGE_TELEMETRY env var is set — DB config cannot override it",
        )
    if db_pool is None:
        raise HTTPException(status_code=503, detail="Database not available")

    pool = await db_pool.get_pool()
    await pool.execute(
        "UPDATE telemetry_config SET enabled = $1, updated_at = NOW(), updated_by = 'admin-api' WHERE id = 1",
        body.enabled,
    )
    return {"success": True, "enabled": body.enabled}


# === Unified Config Dashboard ===


class ConfigSetRequest(BaseModel):
    """Request to set a config value."""

    value: Any = Field(..., description="The new value for the config field")


@router.get("/config")
async def get_config_dashboard(
    _: str = Depends(verify_admin_token),
    registry: ConfigRegistry = Depends(require_config_registry),
):
    """Full config dashboard: all fields with resolved values, sources, and metadata."""
    return {"config": registry.dashboard_view()}


def _admin_subject(token: str) -> str:
    """Fingerprint the admin auth subject for the gateway_config.updated_by audit column.

    The raw token/session string must never land in the DB. Truncated SHA-256
    is enough to correlate edits without revealing the credential.
    """
    if token == "localhost-bypass":
        return "admin-localhost"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return f"admin:{digest}"


@router.put("/config/{key}")
async def set_config_value(
    body: ConfigSetRequest,
    key: str = Path(..., description="Config field name"),
    subject: str = Depends(verify_admin_token),
    registry: ConfigRegistry = Depends(require_config_registry),
):
    """Set a DB-settable config value. Returns the new resolved state."""
    meta = registry.get_field_meta(key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown config key: {key}")
    if not meta.db_settable:
        raise HTTPException(status_code=400, detail=f"Config key '{key}' cannot be set via admin API")

    try:
        new_resolved = await registry.set_db_value(key, body.value, updated_by=_admin_subject(subject))
    except ConfigOverriddenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "name": key,
        "value": "***" if meta.sensitive else new_resolved.value,
        "source": new_resolved.source.value,
    }


@router.delete("/config/{key}")
async def delete_config_value(
    key: str = Path(..., description="Config field name"),
    _: str = Depends(verify_admin_token),
    registry: ConfigRegistry = Depends(require_config_registry),
):
    """Remove a DB override for a config value, falling back to env or default."""
    meta = registry.get_field_meta(key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown config key: {key}")
    if not meta.db_settable:
        raise HTTPException(status_code=400, detail=f"Config key '{key}' is not DB-settable")

    try:
        new_resolved = await registry.delete_db_value(key)
    except ConfigOverriddenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "name": key,
        "value": "***" if meta.sensitive else new_resolved.value,
        "source": new_resolved.source.value,
    }


class WebhookStatsResponse(BaseModel):
    """Webhook delivery stats for operator dashboards / alerting."""

    enabled: bool
    safe_url: str
    pending_depth: int
    dropped_count: int
    # Cumulative count of webhooks that exhausted their retry budget without
    # the receiver acknowledging. Distinct from `dropped_count` (cap-reached
    # drop) and `permanent_failure_count` (4xx misconfig — receiver rejected).
    # Sum the three for the true loss rate.
    gave_up_count: int
    permanent_failure_count: int
    # Cumulative count of webhooks dropped before reaching the network because
    # payload construction raised (type drift from operator-policy mutation,
    # etc.). The receiver never sees anything; this counter is the only signal.
    payload_build_failure_count: int
    max_pending_tasks: int
    started_at: str
    worker_pid: int


@router.get("/webhook/stats", response_model=WebhookStatsResponse)
async def webhook_stats(
    _: str = Depends(verify_admin_token),
    webhook_sender: WebhookSender | None = Depends(get_webhook_sender),
):
    """Return webhook backpressure / delivery stats.

    `pending_depth` is current in-flight tasks; `dropped_count` is the
    cumulative count of webhooks dropped because the pending-task cap was hit
    (process lifetime — resets on restart). `started_at` is the construction
    timestamp; combine with `dropped_count` to compute a drop rate.

    All counters are **per uvicorn worker**, not gateway-wide. With N workers,
    polling this endpoint via a load balancer returns one worker's view at
    random. For a gateway-wide picture, scrape every worker (or aggregate
    via a metrics backend — Trello c/2GkyAelr tracks the OTel follow-up).
    """
    pid = os.getpid()
    if webhook_sender is None:
        return WebhookStatsResponse(
            enabled=False,
            safe_url="",
            pending_depth=0,
            dropped_count=0,
            gave_up_count=0,
            permanent_failure_count=0,
            payload_build_failure_count=0,
            max_pending_tasks=0,
            started_at="",
            worker_pid=pid,
        )
    return WebhookStatsResponse(
        enabled=webhook_sender.enabled,
        safe_url=webhook_sender.safe_url,
        pending_depth=webhook_sender.pending_depth,
        dropped_count=webhook_sender.dropped_count,
        gave_up_count=webhook_sender.gave_up_count,
        permanent_failure_count=webhook_sender.permanent_failure_count,
        payload_build_failure_count=webhook_sender.payload_build_failure_count,
        max_pending_tasks=webhook_sender.max_pending_tasks,
        started_at=webhook_sender.started_at.isoformat(),
        worker_pid=pid,
    )


__all__ = ["router"]
