"""FastAPI application with HTMX-powered dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Stream Conditions", version="0.1.0")

_templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return _templates.TemplateResponse(  # type: ignore[return-value]
        "index.html", {"request": request}
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
