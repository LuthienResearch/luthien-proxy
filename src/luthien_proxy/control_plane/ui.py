"""HTML template routes for quick debug UIs in the control plane."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter()


@router.get("/debug", response_class=HTMLResponse)
async def debug_browser(request: Request):
    """Render the debug browser page listing debug types."""
    return templates.TemplateResponse(request, "debug_browser.html")


@router.get("/debug/{debug_type}", response_class=HTMLResponse)
async def debug_ui(request: Request, debug_type: str):
    """Render the single-type debug page for the given type."""
    return templates.TemplateResponse(request, "debug_single.html", {"debug_type": debug_type})


# Removed request_logs UI


@router.get("/hooks/trace", response_class=HTMLResponse)
async def hooks_trace_ui(request: Request):
    """Render the hooks trace UI for a given call ID."""
    return templates.TemplateResponse(request, "hooks_trace.html")


@router.get("/hooks/conversation", response_class=HTMLResponse)
async def hooks_conversation_ui(request: Request):
    """Render the live conversation impact view."""
    return templates.TemplateResponse(request, "conversation_view.html")
