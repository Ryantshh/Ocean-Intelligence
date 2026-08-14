"""Read-only endpoints backing the dashboard page.

Queries the same environment schema Chainlit writes to, so the numbers always
describe the environment the chat is actually using.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ai_platform.app.data_layer import get_schema_name

router = APIRouter(prefix="/api", tags=["dashboard"])

COUNT_QUERY = text(
    """
    SELECT
        (SELECT count(*) FROM users) AS users,
        (SELECT count(*) FROM threads) AS threads,
        (SELECT count(*) FROM steps) AS steps,
        (SELECT count(*) FROM feedbacks) AS feedbacks
    """
)


def get_engine_url() -> str:
    """Read the database URL shared with the Chainlit data layer.

    Returns
    -------
    str
        SQLAlchemy asyncpg URL.

    Raises
    ------
    RuntimeError
        If ``CHAINLIT_DATABASE_URL`` is unset.
    """
    url = os.environ.get("CHAINLIT_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("CHAINLIT_DATABASE_URL is not set. Check .env.")
    return url


@router.get("/stats")
async def read_stats() -> dict[str, Any]:
    """Return row counts for the current environment.

    Returns
    -------
    dict
        Schema name and a count per Chainlit table.

    Raises
    ------
    HTTPException
        502 when the database cannot be reached or queried.
    """
    schema = get_schema_name()
    engine = create_async_engine(
        get_engine_url(),
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.connect() as connection:
            row = (await connection.execute(COUNT_QUERY)).mappings().one()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await engine.dispose()

    return {"schema": schema, "counts": dict(row)}
