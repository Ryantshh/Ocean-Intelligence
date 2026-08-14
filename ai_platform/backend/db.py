"""Read-only access to the fleet data.

Shares the Postgres instance Chainlit persists to, but reads ``public`` where
the Bronze-to-Silver pipeline lands orders and tonnage.
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg
from dotenv import load_dotenv

load_dotenv()


def get_dsn() -> str:
    """Read the connection string in the form asyncpg expects.

    ``CHAINLIT_DATABASE_URL`` carries the SQLAlchemy ``+asyncpg`` driver marker,
    which asyncpg itself rejects.

    Returns
    -------
    str
        A plain ``postgresql://`` DSN.

    Raises
    ------
    RuntimeError
        If ``CHAINLIT_DATABASE_URL`` is unset.
    """
    url = os.environ.get("CHAINLIT_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("CHAINLIT_DATABASE_URL is not set. Check .env.")
    return url.replace("postgresql+asyncpg://", "postgresql://")


TIMEOUT_SECONDS = 30


async def fetch_rows(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Run a read-only query and return plain dictionaries.

    Opens and closes a connection per call. Fine at this volume; a pool belongs
    here once query rate justifies one.

    Parameters
    ----------
    sql : str
        Parameterised statement built by ``build_sql``.
    params : list
        Positional parameters.

    Returns
    -------
    list of dict
        Result rows, JSON-friendly.
    """
    connection = await asyncpg.connect(get_dsn(), timeout=TIMEOUT_SECONDS)
    try:
        records = await connection.fetch(sql, *params, timeout=TIMEOUT_SECONDS)
    finally:
        await connection.close()
    return [dict(record) for record in records]
