"""FastAPI application hosting the dashboard and the mounted chat.

Ordering is load-bearing. Routes must be registered before ``mount_chainlit``;
anything declared after it returns 404. ``root_path`` must stay unset or
mounting fails outright.

The dashboard is served by an explicit route rather than ``StaticFiles`` mounted
at ``/``. A mount at ``/`` matches every path, so it shadows ``/chat`` and
Chainlit never sees the request.
"""

from __future__ import annotations

from pathlib import Path

from chainlit.utils import mount_chainlit
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ai_platform.app.api.dashboard import router as dashboard_router
from ai_platform.app.api.trader_override import router as trader_override_router

load_dotenv()

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = PACKAGE_ROOT / "dashboard"
CHAINLIT_TARGET = str(PACKAGE_ROOT / "app" / "cl_app.py")

app = FastAPI(title="Ocean Intelligence")


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
app.include_router(trader_override_router)
app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

mount_chainlit(app=app, target=CHAINLIT_TARGET, path="/chat")
