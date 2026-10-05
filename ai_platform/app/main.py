"""FastAPI application hosting the dashboard and the mounted chat.

Ordering is load-bearing. Routes must be registered before ``mount_chainlit``;
anything declared after it returns 404. ``root_path`` must stay unset or
mounting fails outright.

The dashboard is served by an explicit route rather than ``StaticFiles`` mounted
at ``/``. A mount at ``/`` matches every path, so it shadows ``/chat`` and
Chainlit never sees the request.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from chainlit.utils import mount_chainlit
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ai_platform.app.api.dashboard import router as dashboard_router
from ai_platform.app.api.export import router as export_router
from ai_platform.backend.db import close_pool
from ai_platform.order_override.order_override import router as order_override_router
from ai_platform.trader_override.trader_override import router as trader_override_router

load_dotenv()

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = PACKAGE_ROOT / "dashboard"
CHAINLIT_TARGET = str(PACKAGE_ROOT / "app" / "cl_app.py")

app = FastAPI(title="Ocean Intelligence")

CHAINLIT_PUBLIC_PREFIX = "/chat/public/"


@app.middleware("http")
async def revalidate_chainlit_public(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Make browsers revalidate Chainlit's public files on every load.

    Chainlit serves custom elements such as ``Results.jsx`` with no
    ``Cache-Control``, so browsers reuse a stale copy for hours after an edit.
    ``no-cache`` still allows a cheap 304 via the ETag.

    Parameters
    ----------
    request : Request
        Incoming request.
    call_next : Callable
        The rest of the application.

    Returns
    -------
    Response
        The response, with ``Cache-Control: no-cache`` on public files.
    """
    response = await call_next(request)
    if request.url.path.startswith(CHAINLIT_PUBLIC_PREFIX):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.on_event("shutdown")
async def _close_dashboard_pool() -> None:
    """Release the dashboard's asyncpg pool so its connections don't sit
    open against Supabase's session pooler across a `--reload` restart.

    See ``ai_platform/backend/db.py``'s ``close_pool`` docstring -- skipping
    this is what let repeated restarts during manual testing pile up
    against the project-wide EMAXCONNSESSION cap.
    """
    await close_pool()


@app.get("/health")
def read_health() -> dict[str, str]:
    """Report that the web layer is serving.

    Returns
    -------
    dict
        Fixed status payload.
    """
    return {"status": "ok"}


@app.get("/")
def read_dashboard() -> FileResponse:
    """Serve the dashboard page.

    Returns
    -------
    FileResponse
        ``static/index.html``.
    """
    return FileResponse(DASHBOARD_DIR / "index.html")


app.include_router(dashboard_router)
app.include_router(export_router)
app.include_router(trader_override_router)
app.include_router(order_override_router)
app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

mount_chainlit(app=app, target=CHAINLIT_TARGET, path="/chat")
