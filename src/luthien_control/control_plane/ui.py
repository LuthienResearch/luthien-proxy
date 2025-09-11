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
    return templates.TemplateResponse("debug_browser.html", {"request": request})


@router.get("/debug/{debug_type}", response_class=HTMLResponse)
async def debug_ui(request: Request, debug_type: str):
    return templates.TemplateResponse(
        "debug_single.html", {"request": request, "debug_type": debug_type}
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_ui(request: Request):
    return templates.TemplateResponse("logs.html", {"request": request})


@router.get("/hooks/trace", response_class=HTMLResponse)
async def hooks_trace_ui(request: Request):
    return templates.TemplateResponse("hooks_trace.html", {"request": request})
