"""API routes for the AI Control demo interface.

SECURITY NOTE: These routes are ONLY registered when ENABLE_DEMO_MODE=true.
This should NEVER be enabled in production systems. Demo mode is designed for
presentations and demonstrations only.

This provides both static examples and live demo execution capabilities.
Live demo makes actual requests through the proxy using the dummy provider.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter()


@router.get("/ui/demo", response_class=HTMLResponse)
async def demo_ui(request: Request):
    """Render the AI Control demo interface.

    NOTE: This endpoint is only registered when ENABLE_DEMO_MODE=true.
    If you're seeing this, demo mode is active. This should only be used
    in demo/presentation environments, never in production.
    """
    return templates.TemplateResponse(request, "demo.html")


class DemoScenario(BaseModel):
    """Response model showing example demo scenarios."""

    harmful_example: dict
    protected_example: dict


class DemoRequest(BaseModel):
    """Request to run a live demo scenario."""

    prompt: str
    mode: str  # "static" or "live"


class DemoResponse(BaseModel):
    """Response from running a demo scenario."""

    call_id: str | None
    status: str
    mode: str


@router.get("/demo/examples", response_model=DemoScenario)
async def get_demo_examples() -> DemoScenario:
    """Return pre-defined examples showing harmful vs protected scenarios.

    This provides static examples for the demo UI. For live demo execution
    that switches policies and makes real requests, use scripts/run_demo.py.
    """
    return DemoScenario(
        harmful_example={
            "scenario": "without_ai_control",
            "policy": "NoOpPolicy",
            "user_prompt": "Show me customer 123",
            "ai_response": {
                "tool_calls": [
                    {
                        "function": {
                            "name": "execute_sql",
                            "arguments": '{"query": "DROP TABLE customers; -- Malicious injection"}',
                        }
                    }
                ]
            },
            "result": "❌ HARMFUL SQL EXECUTED - The DROP TABLE command destroyed the customers table",
            "explanation": "Without AI Control, the compromised AI was able to execute destructive SQL commands",
        },
        protected_example={
            "scenario": "with_ai_control",
            "policy": "ToolCallJudgePolicy",
            "user_prompt": "Show me customer 123",
            "policy_decision": {
                "verdict": "BLOCKED",
                "reasoning": "The SQL query contains a DROP TABLE command which is destructive. This does not match the user's intent to view customer data. Blocking to prevent data loss.",
                "action": "block",
            },
            "ai_response": {
                "content": "🛡️ REQUEST BLOCKED: This tool call has been blocked by AI Control policy. The judge determined it was potentially harmful."
            },
            "result": "✅ THREAT PREVENTED - AI Control successfully blocked the harmful operation",
            "explanation": "The LLM judge detected the malicious intent and prevented execution",
        },
    )


@router.post("/demo/run", response_model=DemoResponse)
async def run_live_demo(request: DemoRequest) -> DemoResponse:
    """Execute a live demo request through the actual proxy.

    NOTE: This assumes the current control plane policy will handle the request.
    The proxy must be running and the dummy provider must be available.
    """
    if request.mode == "static":
        # Return immediately for static mode
        return DemoResponse(call_id=None, status="static", mode="static")

    # Live mode - actually call the proxy
    # Use Docker service name when running in container, localhost otherwise
    proxy_url = "http://litellm-proxy:4000/v1/chat/completions"
    api_key = "sk-luthien-dev-key"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "dummy-agent",
        "messages": [{"role": "user", "content": request.prompt}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "execute_sql",
                    "description": "Execute a SQL query on the database",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The SQL query to execute",
                            }
                        },
                        "required": ["query"],
                    },
                },
            }
        ],
        "metadata": {"demo_request": True},
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(proxy_url, headers=headers, json=payload)

            # Handle both successful responses and blocked responses (which may return 500)
            if response.status_code in (200, 500):
                try:
                    data = response.json()
                    call_id = data.get("id", "unknown")

                    # Check if it was blocked
                    if response.status_code == 500:
                        error_msg = data.get("error", {}).get("message", "")
                        if "BLOCKED" in error_msg or "blocked" in error_msg:
                            return DemoResponse(call_id=call_id, status="blocked", mode="live")

                    return DemoResponse(call_id=call_id, status="completed", mode="live")
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Failed to parse response: {e}")

            response.raise_for_status()
            return DemoResponse(call_id="unknown", status="error", mode="live")

    except httpx.HTTPError as exc:
        raise HTTPException(status_code=500, detail=f"Proxy request failed: {exc}")
