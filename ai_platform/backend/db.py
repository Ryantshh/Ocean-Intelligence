"""Read-only access to the fleet data.

Shares the Postgres instance Chainlit persists to, but reads ``public`` where
the Bronze-to-Silver pipeline lands orders and tonnage.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import asyncpg
from dotenv import load_dotenv

load_dotenv()


def json_safe(value: Any) -> Any:
    """Convert a driver value into something ``json.dumps`` accepts.

    ``CustomElement.__post_init__`` serialises props with a bare ``json.dumps``
    and no ``default``, so an unconverted date raises before the element is ever
    sent. Dates become ISO strings, which also sort chronologically as strings
    and so need no special handling in the table.

    Decimals become native numbers. Left alone they serialise as strings and the
    table sorts them lexicographically — 90,000 tonnes would rank above 187,000.
    Integral values become ``int`` rather than ``float`` because ``order_id`` on
    tonnage is numeric and runs to eighteen digits, which a float corrupts.

    Parameters
    ----------
    value : Any
        Cell value straight from the driver.

    Returns
    -------
    Any
        A JSON-serialisable equivalent, or the value untouched.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time.min else value.isoformat(" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


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
        Result rows, with driver types coerced so ``json.dumps`` accepts them.
    """
    connection = await asyncpg.connect(get_dsn(), timeout=TIMEOUT_SECONDS)
    try:
        records = await connection.fetch(sql, *params, timeout=TIMEOUT_SECONDS)
    finally:
        await connection.close()
    return [
        {column: json_safe(value) for column, value in record.items()}
        for record in records
    ]
