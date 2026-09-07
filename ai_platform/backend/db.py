"""Read-only access to the fleet data.

Shares the Postgres instance Chainlit persists to, but reads ``public`` where
the Bronze-to-Silver pipeline lands orders and tonnage.
"""

from __future__ import annotations

import asyncio
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

# Supabase's pooler caps concurrent connections project-wide (session mode,
# a fixed pool_size -- confirmed live at 15, shared with Chainlit's own
# connections against the same CHAINLIT_DATABASE_URL). Opening a fresh
# connection per query (the old behaviour here) hit that cap directly: a
# single dashboard page load fans out into a dozen-plus concurrent queries
# on its own (change_feed and daily_counts each asyncio.gather six sub-
# queries), which was enough on its own to exceed 15 and produced a live
# "EMAXCONNSESSION ... max clients are limited to pool_size: 15" error.
# A small shared pool bounds this module's own real connection count
# instead -- concurrent callers queue for one of a few connections rather
# than each opening a new one -- capped well under the project-wide limit
# to leave room for Chainlit's own connections. Not explicitly closed on
# process shutdown: there's no app-lifespan hook wired up for it, and an
# abrupt process exit closing these sockets is unremarkable (Postgres
# notices the disconnect and cleans up server-side either way).
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 6

_pool: asyncpg.Pool | None = None
_pool_lock: asyncio.Lock | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None


async def _get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first use.

    Both the pool and its guarding lock are tied to whichever event loop
    was running when they were created -- reusing either from a different
    loop raises confusing asyncpg/asyncio errors (``Event loop is
    closed``, ``another operation is in progress``, ``connection was
    closed in the middle of operation``), confirmed live when this was
    first exercised under a test harness that starts a fresh loop per
    call rather than one loop for the process's whole lifetime (uvicorn
    only ever runs one, so this doesn't come up in normal deployment, but
    the module can't assume that). So both are discarded and recreated
    whenever the running loop doesn't match the one they were built for.
    The stale pool is never explicitly closed in that case -- doing so
    would itself need the dead loop -- its reference is simply dropped;
    same reasoning as not closing it on process shutdown, below.

    A lock (not just a `_pool is None` check) guards the actual creation
    so two concurrent first-callers on the same loop can't each start
    creating a pool -- only one would win with asyncpg, but the other's
    reference would leak.

    Returns
    -------
    asyncpg.Pool
        The shared pool for the current event loop.
    """
    global _pool, _pool_lock, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool_loop is not loop:
        _pool = None
        _pool_lock = asyncio.Lock()
        _pool_loop = loop
    if _pool is None:
        async with _pool_lock:
            if _pool is None:  # re-check: another task may have created it while this one waited
                _pool = await asyncpg.create_pool(
                    get_dsn(),
                    min_size=_POOL_MIN_SIZE,
                    max_size=_POOL_MAX_SIZE,
                    timeout=TIMEOUT_SECONDS,
                )
    return _pool


async def fetch_rows(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Run a read-only query and return plain dictionaries.

    Acquires a connection from the shared pool (see :func:`_get_pool`)
    rather than opening a new one per call.

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
    pool = await _get_pool()
    async with pool.acquire(timeout=TIMEOUT_SECONDS) as connection:
        records = await connection.fetch(sql, *params, timeout=TIMEOUT_SECONDS)
    return [dict(record) for record in records]
