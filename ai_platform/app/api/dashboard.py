"""Read-only endpoints backing the dashboard page.

Queries the same environment schema Chainlit writes to, so the numbers always
describe the environment the chat is actually using.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ai_platform.app.data_layer import get_schema_name
from ai_platform.backend import dashboard_queries as dq
from ai_platform.backend.db import fetch_rows

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


# ---------------------------------------------------------------------------
# Status Overview -- reads infra/sql/dashboard_gold_views.sql's views (plus
# public.order_test directly, for the parts of the change feed the views
# don't cover) via asyncpg. Unlike ai_platform.backend.nodes's chat-agent
# reads (which go through public.tonnage/public."order"), this reads the
# tonnage_test/order_test source instead -- see dashboard_gold_views.sql's
# header for why. Unlike /api/stats above, these never touch the Chainlit
# schema or SQLAlchemy -- see ai_platform/backend/db.py's docstring for why
# the two paths are kept independent.
# ---------------------------------------------------------------------------


async def _run(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Execute one dashboard query and surface failures as a 502.

    Parameters
    ----------
    sql : str
        Parameterised statement from ``ai_platform.backend.dashboard_queries``.
    params : list
        Positional parameters.

    Returns
    -------
    list of dict
        Result rows.

    Raises
    ------
    HTTPException
        502 when the database cannot be reached or queried -- most commonly
        because ``infra/sql/dashboard_gold_views.sql`` has not been applied
        to this database yet, which surfaces as an ``undefined table`` error
        on any of these views.
    """
    try:
        return await fetch_rows(sql, params)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/dashboard/vessels")
async def list_vessels(
    status: dq.DashboardStatus | None = None,
    region: str | None = None,
    sort: dq.SortKey = "update_date",
    stale: bool | None = None,
    conflict: bool | None = None,
) -> list[dict[str, Any]]:
    """Vessel tracker: current status per vessel, filterable by status/region.

    Parameters
    ----------
    status : "FIXED", "OPEN", "ON SUBS", or None
        Exact match. Omit for every status.
    region : str or None
        Case-insensitive substring match against any one of a vessel's
        (possibly several) parent zones -- e.g. "east" matches both
        "East Africa" and "Far East".
    sort : "eta", "update_date", or "open_date_end"
        Sort key; defaults to most recently updated first.
    stale : bool or None
        Restricts to (or excludes) ``is_stale`` rows. Backs the summary
        panel's Stale KPI tile.
    conflict : bool or None
        Same shape as ``stale`` but against ``has_conflicting_reports``.
        Backs the summary panel's Conflicts KPI tile.

    Returns
    -------
    list of dict
        One row per vessel.
    """
    sql, params = dq.vessels_sql(status, region, sort, stale=stale, conflict=conflict)
    return await _run(sql, params)


@router.get("/dashboard/vessels/flag-counts")
async def vessel_flag_counts() -> dict[str, int]:
    """Fleet-wide stale / conflicting-reports counts, for the summary panel's KPI tiles.

    Returns
    -------
    dict
        ``{"stale": n, "conflicts": n}``.
    """
    row = (await _run(*dq.vessel_flag_counts_sql()))[0]
    return {"stale": row["stale"], "conflicts": row["conflicts"]}


@router.get("/dashboard/vessels/status-counts")
async def vessel_status_counts() -> dict[str, int]:
    """Fleet-wide FIXED/OPEN/ON SUBS counts, for the tracker's summary tiles.

    A trader should see the shape of the market without opening the vessel
    table -- this backs that, independent of whatever status/region filter
    the table itself currently has applied.

    Returns
    -------
    dict
        ``{"FIXED": n, "OPEN": n, "ON SUBS": n}`` -- always all three
        keys, 0 for a status with no vessels right now rather than an
        absent key.
    """
    rows = await _run(*dq.status_counts_sql())
    counts = {"FIXED": 0, "OPEN": 0, "ON SUBS": 0}
    counts.update({row["dashboard_status"]: row["vessel_count"] for row in rows})
    return counts


@router.get("/dashboard/daily-counts")
async def daily_counts(days: int = 14) -> dict[str, Any]:
    """Day-bucketed trend series for the summary panel's Daily Trends charts.

    Three independent series -- new vessels, FIXED/OPEN/ON SUBS transitions,
    and new orders -- each bucketed by calendar day over the trailing
    ``days`` days ending at that series' own simulated "now" (tonnage- and
    orders-side "now" are computed separately, same as :func:`change_feed`,
    even though both currently resolve to the same instant).

    Parameters
    ----------
    days : int
        Trailing window length in calendar days, ``until``'s own day
        included. Defaults to 14.

    Returns
    -------
    dict
        ``days`` echoed back, plus ``new_vessels``, ``status_changes``, and
        ``new_orders`` -- each a list of ``{"day": date, "count": int}``
        rows, one per calendar day in the range (zero-filled, never sparse).
    """
    reference = (await _run(*dq.reference_times_sql()))[0]
    tonnage_now = reference["tonnage_now"]
    orders_now = reference["orders_now"]

    tonnage_since = dq.daily_range_start(tonnage_now, days)
    orders_since = dq.daily_range_start(orders_now, days)
    tonnage_until = tonnage_now.replace(tzinfo=None)
    orders_until = orders_now.replace(tzinfo=None)

    new_vessels, status_changes, new_orders = await asyncio.gather(
        _run(*dq.daily_new_vessels_sql(tonnage_since, tonnage_until)),
        _run(*dq.daily_status_changes_sql(tonnage_since, tonnage_until)),
        _run(*dq.daily_new_orders_sql(orders_since, orders_until)),
    )
    return {
        "days": days,
        "new_vessels": new_vessels,
        "status_changes": status_changes,
        "new_orders": new_orders,
    }


@router.get("/dashboard/regions")
async def regional_supply_demand() -> list[dict[str, Any]]:
    """Regional supply (open vessels) vs. demand (recent orders), per region.

    Returns
    -------
    list of dict
        One row per region with ``supply`` and ``demand`` counts.
    """
    sql, params = dq.regions_sql()
    return await _run(sql, params)


@router.get("/dashboard/ecsa")
async def ecsa_ballasters(sort: dq.SortKey = "eta") -> list[dict[str, Any]]:
    """Every vessel currently open in East Coast South America.

    Parameters
    ----------
    sort : "eta", "update_date", or "open_date_end"
        Sort key; defaults to soonest ETA first.

    Returns
    -------
    list of dict
        One row per ballaster, same shape as :func:`list_vessels`.
    """
    sql, params = dq.ecsa_ballasters_sql(sort)
    return await _run(sql, params)


@router.get("/dashboard/ecsa/{vessel_id}/history")
async def ecsa_vessel_history(vessel_id: str) -> list[dict[str, Any]]:
    """Status history trail for one vessel (e.g. On Subs -> Open -> Fixed).

    Parameters
    ----------
    vessel_id : str
        Vessel identifier, e.g. "VESSEL 0663".

    Returns
    -------
    list of dict
        One row per status transition, oldest first.
    """
    sql, params = dq.ecsa_history_sql(vessel_id)
    return await _run(sql, params)


@router.get("/dashboard/changes")
async def change_feed(window: dq.ChangeWindow = "dod") -> dict[str, Any]:
    """Day-on-day or week-on-week change feed.

    ``vessels_no_longer_fresh`` is presented in the UI as "Removed" (per
    the product spec) but is really only an inferred "stopped reporting
    recently" signal -- nothing in the source data marks a record
    withdrawn. See
    ``ai_platform.backend.dashboard_queries.vessels_no_longer_fresh_sql``.

    ``field_changes`` covers everything *except* the FIXED/OPEN/ON SUBS
    transition (that's ``vessel_status_changes``, kept separate so the same
    event isn't reported twice) -- open area, DWT, destination, ETA,
    parent zone, and ballast/laden, per vessel report vs. its immediately
    preceding one.

    "Now" is simulated, not real wall-clock time: treated as real time minus
    one year (see ``ai_platform.backend.dashboard_queries.reference_times_sql``
    for the full rationale), an explicit simulation request rather than a
    data-driven default. Rows dated on or after that simulated "now" are
    excluded outright, not merely deprioritised.

    Parameters
    ----------
    window : "dod" or "wow"
        Day-on-day (last 24h) or week-on-week (last 7d), each measured from
        the simulated "now".

    Returns
    -------
    dict
        ``window``, the two simulated reference instants and window starts
        (``tonnage_reference_now``, ``tonnage_since``, ``orders_reference_now``,
        ``orders_since``), and six row lists: ``new_vessels``,
        ``vessel_status_changes``, ``vessels_no_longer_fresh``,
        ``field_changes``, ``new_orders``, ``amended_orders``.
    """
    reference = (await _run(*dq.reference_times_sql()))[0]
    tonnage_now = reference["tonnage_now"]
    orders_now = reference["orders_now"]

    tonnage_since = dq.window_start(window, now=tonnage_now)
    orders_since = dq.window_start(window, now=orders_now)
    length = dq.window_length(window)
    # public.order_test / public.tonnage_test's timestamp columns are naive
    # (see dashboard_queries.py's window_start() docstring) -- tonnage_now/
    # orders_now are still the tz-aware values asyncpg decoded from
    # timestamptz, so both have to be stripped the same way window_start()
    # strips `since` before they can bind as `until` bounds below.
    tonnage_until = tonnage_now.replace(tzinfo=None)
    orders_until = orders_now.replace(tzinfo=None)

    (
        new_vessels,
        vessel_status_changes,
        vessels_no_longer_fresh,
        field_changes,
        new_orders,
        amended_orders,
    ) = await asyncio.gather(
        _run(*dq.new_vessels_sql(tonnage_since)),
        _run(*dq.vessel_status_changes_sql(tonnage_since)),
        _run(*dq.vessels_no_longer_fresh_sql(tonnage_since, length)),
        _run(*dq.vessel_field_changes_sql(tonnage_since, tonnage_until)),
        _run(*dq.new_orders_sql(orders_since, orders_until)),
        _run(*dq.amended_orders_sql(orders_since, orders_until)),
    )
    return {
        "window": window,
        "tonnage_reference_now": tonnage_now.isoformat(),
        "tonnage_since": tonnage_since.isoformat(),
        "orders_reference_now": orders_now.isoformat(),
        "orders_since": orders_since.isoformat(),
        "new_vessels": new_vessels,
        "vessel_status_changes": vessel_status_changes,
        "vessels_no_longer_fresh": vessels_no_longer_fresh,
        "field_changes": field_changes,
        "new_orders": new_orders,
        "amended_orders": amended_orders,
    }
