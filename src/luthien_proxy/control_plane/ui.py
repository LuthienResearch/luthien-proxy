"""HTML template routes for quick debug UIs in the control plane."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter()


@router.get("/ui", response_class=HTMLResponse)
async def ui_index(request: Request):
    """Render a simple index that links to the available UI endpoints."""
    ui_paths = sorted(
        {route.path for route in request.app.routes if isinstance(route, APIRoute) and route.path.startswith("/ui/")}
    )

    context = {"request": request, "ui_paths": ui_paths}
    return templates.TemplateResponse(request, "ui_index.html", context)


@router.get("/debug", response_class=HTMLResponse)
async def debug_browser(request: Request):
    """Render the debug browser page listing debug types."""
    return templates.TemplateResponse(request, "debug_browser.html")


@router.get("/debug/{debug_type}", response_class=HTMLResponse)
async def debug_ui(request: Request, debug_type: str):
    """Render the single-type debug page for the given type."""
    return templates.TemplateResponse(request, "debug_single.html", {"debug_type": debug_type})


# Removed request_logs UI


@router.get("/ui/hooks/trace", response_class=HTMLResponse)
async def hooks_trace_ui(request: Request):
    """Render the hooks trace UI for a given call ID."""
    return templates.TemplateResponse(request, "hooks_trace.html")


@router.get("/ui/conversation/by_trace", response_class=HTMLResponse)
async def conversation_by_trace_ui(request: Request):
    """Render the conversation view grouped by trace id."""
    return templates.TemplateResponse(request, "conversation_by_trace.html")


@router.get("/ui/conversation", response_class=HTMLResponse)
async def hooks_conversation_ui(request: Request):
    """Render the live conversation impact view."""
    return templates.TemplateResponse(request, "conversation_view.html")


@router.get("/ui/conversation/logs", response_class=HTMLResponse)
async def conversation_logs_ui(request: Request):
    """Render a simple view over recorded conversation turns."""
    return templates.TemplateResponse(request, "conversation_logs.html")


@router.get("/ui/conversation/live", response_class=HTMLResponse)
async def conversation_monitor_ui(request: Request):
    """Render the consolidated live conversation monitor."""
    return templates.TemplateResponse(request, "conversation_monitor.html")


@router.get("/ui/tool-calls", response_class=HTMLResponse)
async def tool_call_logs_ui(request: Request):
    """Render a view over recorded tool-call interventions."""
    return templates.TemplateResponse(request, "tool_call_logs.html")


@router.get("/ui/policy/judge", response_class=HTMLResponse)
async def judge_policy_ui(request: Request):
    """Render a view of LLM judge policy decisions for a trace."""
    return templates.TemplateResponse(request, "policy_judge.html")
