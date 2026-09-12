"""Read-only endpoints backing the dashboard page.

Queries the same environment schema Chainlit writes to, so the numbers always
describe the environment the chat is actually using.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
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
    # pool_size/max_overflow pinned small: this engine is built fresh per
    # call and only ever needs one connection at a time. Left at
    # create_async_engine's defaults (pool_size=5, max_overflow=10) it
    # could itself claim up to 15 connections under concurrent hits --
    # Supabase's entire project-wide session-pooler cap on its own. See
    # ai_platform/backend/db.py's pool comment for that cap.
    engine = create_async_engine(
        get_engine_url(),
        connect_args={"server_settings": {"search_path": schema}},
        pool_size=1,
        max_overflow=0,
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
) -> list[dict[str, Any]]:
    """Vessel tracker: current status per vessel, filterable by status/region.

    Parameters
    ----------
    status : "FIXED", "OPEN", "ON SUBS", "LIKELY FIXED", or None
        Exact match. Omit for every status.
    region : str or None
        Case-insensitive substring match against any one of a vessel's
        (possibly several) parent zones -- e.g. "east" matches both
        "East Africa" and "Far East".
    sort : "eta", "update_date", or "open_date_end"
        Sort key; defaults to most recently updated first.

    Returns
    -------
    list of dict
        One row per vessel.
    """
    sql, params = dq.vessels_sql(status, region, sort)
    return await _run(sql, params)


@router.get("/dashboard/vessels/by-day")
async def vessels_by_day(
    metric: dq.ClickableDailyMetric,
    day: date,
    range_since: datetime | None = None,
    range_until: datetime | None = None,
) -> list[dict[str, Any]]:
    """Vessel tracker rows for the vessels counted in one Daily Trends bar.

    Backs clicking a "New Vessels" or "Status Changes" bar in the Daily
    Trends chart: the vessel tracker below switches to showing exactly the
    vessels that bar counted, at their current status (see
    :func:`ai_platform.backend.dashboard_queries.vessels_on_day_sql` for why
    current rather than as-of-that-day). New Orders has no vessel-level
    equivalent, so ``metric`` only accepts the other two.

    Parameters
    ----------
    metric : "new_vessels" or "status_changes"
        Which Daily Trends bar was clicked.
    day : date
        The calendar day the clicked bar represents, e.g. ``2025-08-29``.
    range_since, range_until : datetime or None
        The same ``range.since``/``range.until`` :func:`daily_counts`
        returned alongside the chart data -- the caller should echo them
        back unchanged so the clicked day's bounds are clipped exactly the
        way that bar's own count was, including at the 14-day window's
        (partial) first and last days. Omit only for an ad-hoc lookup
        outside the chart, where a plain midnight-to-midnight day is fine.

    Returns
    -------
    list of dict
        Same shape as :func:`list_vessels`, one row per matched vessel.
    """
    sql, params = dq.vessels_on_day_sql(metric, day, range_since=range_since, range_until=range_until)
    return await _run(sql, params)


@router.get("/dashboard/orders")
async def list_orders(
    region: str | None = None,
    sort: dq.OrderSortKey = "date_received",
) -> list[dict[str, Any]]:
    """Order tracker: non-future orders, filterable by region.

    Parameters
    ----------
    region : str or None
        Case-insensitive substring match against ``load_zone``.
    sort : "date_received" or "laycan_start"
        Sort key; defaults to most recently received first.

    Returns
    -------
    list of dict
        One row per order.
    """
    sql, params = dq.orders_sql(region, sort)
    return await _run(sql, params)


@router.get("/dashboard/orders/by-day")
async def orders_by_day(
    day: date,
    range_since: datetime | None = None,
    range_until: datetime | None = None,
) -> list[dict[str, Any]]:
    """Order tracker rows for the orders counted in one Daily Trends bar.

    Backs clicking the "New Orders" bar in the Daily Trends chart: the
    order tracker below switches to showing exactly the orders that bar
    counted.

    Parameters
    ----------
    day : date
        The calendar day the clicked bar represents.
    range_since, range_until : datetime or None
        The same ``orders_range.since``/``orders_range.until``
        :func:`daily_counts` returned alongside the chart data -- echo
        them back unchanged so the clicked day's bounds match that bar's
        own count exactly, including at the 14-day window's (partial)
        first and last days.

    Returns
    -------
    list of dict
        Same shape as :func:`list_orders`, one row per matched order.
    """
    sql, params = dq.orders_on_day_sql(day, range_since=range_since, range_until=range_until)
    return await _run(sql, params)


@router.get("/dashboard/vessels/status-counts")
async def vessel_status_counts() -> dict[str, int]:
    """Fleet-wide FIXED/OPEN/ON SUBS/LIKELY FIXED counts, for the tracker's summary tiles.

    A trader should see the shape of the market without opening the vessel
    table -- this backs that, independent of whatever status/region filter
    the table itself currently has applied.

    Returns
    -------
    dict
        ``{"FIXED": n, "OPEN": n, "ON SUBS": n, "LIKELY FIXED": n}`` --
        always all four keys, 0 for a status with no vessels right now
        rather than an absent key.
    """
    rows = await _run(*dq.status_counts_sql())
    counts = {"FIXED": 0, "OPEN": 0, "ON SUBS": 0, "LIKELY FIXED": 0}
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
        ``days`` echoed back; ``range`` and ``orders_range`` (each
        ``{"since": datetime, "until": datetime}``) -- the tonnage-side
        bounds ``new_vessels``/``status_changes`` were computed from, and
        the orders-side bounds ``new_orders`` was, respectively. A caller
        driving the chart-bar click-to-filter interaction must echo the
        matching one back to ``GET /dashboard/vessels/by-day`` or
        ``GET /dashboard/orders/by-day`` unchanged, since the first and
        last bars are partial days clipped to exactly that range, not full
        calendar days; plus ``new_vessels``, ``status_changes``, and
        ``new_orders`` -- each a list of one row per calendar day in the
        range (zero-filled, never sparse):
        ``{"day": date, "count": int, "regions": [{"region": str, "count": int}, ...]}``.
        ``regions`` is sorted by descending count and backs the chart's
        hover breakdown; a vessel/order spanning multiple regions is
        counted once per region there, so its total can exceed ``count``
        -- see :func:`ai_platform.backend.dashboard_queries.daily_new_vessels_by_region_sql`.
    """
    reference = (await _run(*dq.reference_times_sql()))[0]
    # fetch_rows() coerces every datetime to an ISO string (json_safe) so
    # the chat agent's CustomElement can json.dumps it directly -- fine for
    # every other dashboard row, but these two specifically get arithmetic
    # done on them below, so they're parsed straight back into real
    # datetimes here rather than working around fetch_rows for just this
    # one query.
    tonnage_now = datetime.fromisoformat(reference["tonnage_now"])
    orders_now = datetime.fromisoformat(reference["orders_now"])

    tonnage_since = dq.daily_range_start(tonnage_now, days)
    orders_since = dq.daily_range_start(orders_now, days)
    tonnage_until = tonnage_now.replace(tzinfo=None)
    orders_until = orders_now.replace(tzinfo=None)

    (
        new_vessels,
        status_changes,
        new_orders,
        new_vessels_by_region,
        status_changes_by_region,
        new_orders_by_region,
    ) = await asyncio.gather(
        _run(*dq.daily_new_vessels_sql(tonnage_since, tonnage_until)),
        _run(*dq.daily_status_changes_sql(tonnage_since, tonnage_until)),
        _run(*dq.daily_new_orders_sql(orders_since, orders_until)),
        _run(*dq.daily_new_vessels_by_region_sql(tonnage_since, tonnage_until)),
        _run(*dq.daily_status_changes_by_region_sql(tonnage_since, tonnage_until)),
        _run(*dq.daily_new_orders_by_region_sql(orders_since, orders_until)),
    )
    return {
        "days": days,
        "range": {"since": tonnage_since, "until": tonnage_until},
        "orders_range": {"since": orders_since, "until": orders_until},
        "new_vessels": _with_region_breakdown(new_vessels, new_vessels_by_region),
        "status_changes": _with_region_breakdown(status_changes, status_changes_by_region),
        "new_orders": _with_region_breakdown(new_orders, new_orders_by_region),
    }


def _with_region_breakdown(
    totals: list[dict[str, Any]], breakdown: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge a day-bucketed total series with its per-(day, region) breakdown.

    Parameters
    ----------
    totals : list of dict
        One row per day, from e.g. :func:`ai_platform.backend.dashboard_queries.daily_new_vessels_sql`.
    breakdown : list of dict
        One row per (day, region), from that function's ``_by_region`` companion.

    Returns
    -------
    list of dict
        ``totals``, each row with a ``regions`` key added.
    """
    by_day: dict[Any, list[dict[str, Any]]] = {}
    for row in breakdown:
        by_day.setdefault(row["day"], []).append(
            {"region": row["region"], "count": row["count"]}
        )
    return [{**row, "regions": by_day.get(row["day"], [])} for row in totals]


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

    ``new_vessels`` and ``vessel_status_changes`` intentionally overlap:
    ``vessel_status_changes`` is the complete transition log (any status
    -> any status, including into 'OPEN'), and ``new_vessels`` separately
    highlights the same into-'OPEN' events under their own heading (the
    sponsor's "new vessel" -- a row explicitly declaring the vessel open,
    for trader attention) -- a transition can and should appear under
    both. See
    ``ai_platform.backend.dashboard_queries.vessel_status_changes_sql``.

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
        ``orders_since``), and four row lists: ``new_vessels``,
        ``vessel_status_changes``, ``field_changes``, ``new_orders``.
    """
    reference = (await _run(*dq.reference_times_sql()))[0]
    # See daily_counts()'s matching comment -- fetch_rows() JSON-stringifies
    # datetimes for the chat agent's sake, so these two are parsed back
    # into real datetimes here since window_start() below does arithmetic
    # on them.
    tonnage_now = datetime.fromisoformat(reference["tonnage_now"])
    orders_now = datetime.fromisoformat(reference["orders_now"])

    tonnage_since = dq.window_start(window, now=tonnage_now)
    orders_since = dq.window_start(window, now=orders_now)
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
        field_changes,
        new_orders,
    ) = await asyncio.gather(
        _run(*dq.new_vessels_sql(tonnage_since, tonnage_until)),
        _run(*dq.vessel_status_changes_sql(tonnage_since)),
        _run(*dq.vessel_field_changes_sql(tonnage_since, tonnage_until)),
        _run(*dq.new_orders_sql(orders_since, orders_until)),
    )
    return {
        "window": window,
        "tonnage_reference_now": tonnage_now.isoformat(),
        "tonnage_since": tonnage_since.isoformat(),
        "orders_reference_now": orders_now.isoformat(),
        "orders_since": orders_since.isoformat(),
        "new_vessels": new_vessels,
        "vessel_status_changes": vessel_status_changes,
        "field_changes": field_changes,
        "new_orders": new_orders,
    }
