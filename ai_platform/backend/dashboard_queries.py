"""Parameterised SQL for the Dashboard Status Overview endpoints.

Reads ``vessel_current_status`` / ``vessel_status_history`` / ``ecsa_ballasters``
/ ``regional_supply_demand`` (see ``infra/sql/dashboard_gold_views.sql``, which
must be applied to the database once before these queries will resolve) plus
``public.order_test`` directly for the parts of the change feed the views
don't cover. Reads ``tonnage_test``/``order_test`` -- the vector-embedded
gold tables the deployed gold-loader Lambda actually keeps up to date --
not the plain ``tonnage``/``order`` tables this module used until this
revision: those had no loader anywhere in this repository, turned out to
be a stale/smaller snapshot, and ``tonnage.order_id`` was confirmed live
precision-corrupted (a lossy float round-trip somewhere upstream), unlike
``tonnage_test.order_id``. See ``infra/sql/dashboard_gold_views.sql``'s
header for the full comparison.

Deliberately not part of ``ai_platform.backend.tables`` -- that package's
modules are the chat agent's LLM filter-extraction contract (``Filters`` /
``FIELD_GUIDE`` / ``build_sql`` consumed by the extraction node's structured
output). These queries are plain REST endpoint parameters, never touched by
the model, so they don't belong in that registry.

Every function returns ``(sql, params)`` for ``ai_platform.backend.db.fetch_rows``,
same convention as the table modules. Column names are never taken from a
caller -- only ``status``/``region``/``sort`` values are, and each is checked
against a fixed whitelist before being placed in the SQL text, so nothing
resembling identifier or ORDER BY injection can reach the query even though
placeholders can't parameterise identifiers.

Every window here (:func:`new_vessels_sql`, :func:`vessel_status_changes_sql`,
:func:`vessels_no_longer_fresh_sql`, :func:`vessel_field_changes_sql`,
:func:`new_orders_sql`, :func:`amended_orders_sql`) takes a ``since``
computed by :func:`window_start`
against one of :func:`reference_times_sql`'s two anchors, not real
wall-clock time. "Now" is simulated as real wall-clock time minus one
year (an explicit request, not a data-driven default) -- see
:func:`reference_times_sql` for the full rationale. Rows dated on or
after that simulated "now" are excluded outright wherever a query reads
``public.tonnage_test`` / ``public.order_test`` directly, which is why
:func:`new_orders_sql` and :func:`amended_orders_sql` also take an
``until`` bound.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

DashboardStatus = Literal["FIXED", "OPEN", "ON SUBS"]
SortKey = Literal["eta", "update_date", "open_date_end"]

_SORT_COLUMNS: dict[SortKey, str] = {
    "eta": "eta ASC NULLS LAST",
    "update_date": "update_date DESC NULLS LAST",
    "open_date_end": "open_date_end ASC NULLS LAST",
}

ChangeWindow = Literal["dod", "wow"]

_WINDOW_LENGTHS: dict[ChangeWindow, timedelta] = {
    "dod": timedelta(days=1),
    "wow": timedelta(days=7),
}

# public.order_test columns -- the core (non-embedding) columns it shares
# with public."order" (see ai_platform/backend/tables/orders.py's
# DISPLAY_COLUMNS), minus `assigned` / `assigned_vessel_name`, which are
# 100% null (see README's "Data caveats"), and minus the embedding/
# gold_loaded_at/embedding_source_hash columns unique to this source.
# order_id is cast to text here for the same reason every dashboard view
# casts it: an 18-digit bigint order_id survives Postgres and Python fine
# (confirmed live, e.g. 728280593490835944) but is silently corrupted by
# any browser's JSON.parse once it crosses into JS.
_ORDERS_COLUMNS_SAFE = (
    "order_id::text AS order_id",
    "cargo_type",
    "cargo_weight_min",
    "cargo_weight_max",
    "load_port",
    "load_zone",
    "discharge_port",
    "discharge_parent_zone",
    "laycan_start",
    "laycan_end",
    "date_received",
    "update_date",
    "cargo_description",
)


def reference_times_sql() -> tuple[str, list]:
    """Fetch both simulated "now" anchors in one round trip.

    See ``infra/sql/dashboard_gold_views.sql``'s ``tonnage_reference_now()``
    / ``orders_reference_now()``. Both currently return the same value --
    real wall-clock time minus one year -- an explicit simulation request:
    treat today as if it were one year earlier, and treat any row dated on
    or after that simulated "now" as not having happened yet (excluded
    outright by the views/queries that read it, not merely deprioritised).
    Kept as two separate functions/anchors rather than one shared value so
    tonnage- and order-side behaviour can diverge again later without every
    caller changing.

    Returns
    -------
    tuple
        ``(sql, params)``; the single result row has ``tonnage_now`` and
        ``orders_now`` columns.
    """
    return (
        "SELECT tonnage_reference_now() AS tonnage_now, orders_reference_now() AS orders_now",
        [],
    )


def window_length(window: ChangeWindow) -> timedelta:
    """Public accessor for a window key's length, for callers outside this module.

    Parameters
    ----------
    window : "dod" or "wow"
        Day-on-day or week-on-week.

    Returns
    -------
    timedelta
        Window length.
    """
    return _WINDOW_LENGTHS[window]


def window_start(window: ChangeWindow, *, now: datetime | None = None) -> datetime:
    """Resolve a DoD/WoW window key to its start timestamp.

    Returned as a naive UTC ``datetime`` -- ``order``'s timestamp columns
    are naive ``timestamp`` (not ``timestamptz``), and asyncpg raises on a
    tz-aware value bound against a naive-timestamp column, so tzinfo is
    stripped after computing in UTC. Binds cleanly against ``tonnage``'s
    ``timestamptz`` columns either way (empirically confirmed live).

    Parameters
    ----------
    window : "dod" or "wow"
        Day-on-day or week-on-week.
    now : datetime, optional
        Reference instant. In production this should be one of
        :func:`reference_times_sql`'s two anchors, not the real clock --
        see that function's docstring for why. Falls back to the real
        current time only when the caller has no reference (e.g. tests).

    Returns
    -------
    datetime
        Naive UTC timestamp marking the start of the window.
    """
    reference = now or datetime.now(UTC)
    return (reference - _WINDOW_LENGTHS[window]).replace(tzinfo=None)


def vessels_sql(
    status: DashboardStatus | None,
    region: str | None,
    sort: SortKey,
    *,
    stale: bool | None = None,
) -> tuple[str, list]:
    """Vessel tracker: current status, optionally filtered by status/region.

    Parameters
    ----------
    status : "FIXED", "OPEN", "ON SUBS", or None
        Exact match against ``dashboard_status``.
    region : str or None
        Case-insensitive substring match against the exploded
        ``parent_zones`` array, so a vessel open across multiple regions
        still matches, and "east" matches both "East Africa" and
        "Far East" -- not an exact match, since the raw zone labels
        (e.g. "Far East") are exact-cased and a trader typing "far east"
        should still find them.
    sort : "eta", "update_date", or "open_date_end"
        Column to sort by; whitelisted against ``_SORT_COLUMNS``.
    stale : bool or None
        ``True`` restricts to ``is_stale`` rows, ``False`` to non-stale,
        ``None`` applies no filter. Backs the summary panel's Stale KPI
        tile drilling down into the vessel table.

    Returns
    -------
    tuple
        ``(sql, params)`` for :func:`ai_platform.backend.db.fetch_rows`.
    """
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        params.append(status)
        clauses.append(f"dashboard_status = ${len(params)}")
    if region is not None:
        params.append(region)
        clauses.append(
            f"EXISTS (SELECT 1 FROM unnest(parent_zones) AS z WHERE z ILIKE '%' || ${len(params)} || '%')"
        )
    if stale is not None:
        clauses.append("is_stale" if stale else "NOT is_stale")
    where_sql = " AND ".join(clauses) if clauses else "TRUE"
    order_sql = _SORT_COLUMNS[sort]
    sql = f"SELECT * FROM vessel_current_status WHERE {where_sql} ORDER BY {order_sql}"
    return sql, params


def regions_sql() -> tuple[str, list]:
    """Regional supply/demand tile.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    return "SELECT * FROM regional_supply_demand", []


def status_counts_sql() -> tuple[str, list]:
    """Fleet-wide FIXED/OPEN/ON SUBS breakdown -- the vessel tracker's summary tiles.

    Backed by ``vessel_status_counts`` (a thin GROUP BY over
    ``vessel_current_status``), so a trader sees the shape of the market
    without opening the vessel table at all.

    Returns
    -------
    tuple
        ``(sql, params)``; one row per status that has at least one
        vessel -- a status with zero vessels right now (e.g. ON SUBS,
        currently) simply doesn't appear, callers should default it to 0.
    """
    return "SELECT * FROM vessel_status_counts", []


def vessel_flag_counts_sql() -> tuple[str, list]:
    """Fleet-wide stale-vessel count -- the summary panel's Stale KPI tile.

    Kept separate from :func:`status_counts_sql` rather than merged into one
    payload -- staleness is a flag a vessel can carry regardless of its
    FIXED/OPEN/ON SUBS status (a FIXED vessel can still be stale), not
    another value of the same ``dashboard_status`` dimension.

    (There used to be a second flag here, conflicting reports -- removed:
    once overlapping/disagreeing rows are resolved by recency, per
    ``vessel_current_status``'s ``active_bookings`` CTE and
    ``vessel_status_history``'s timeline, there's nothing left that's
    still ambiguous, so nothing left to flag.)

    Returns
    -------
    tuple
        ``(sql, params)``; one row with a ``stale`` count.
    """
    return (
        "SELECT COUNT(*) FILTER (WHERE is_stale) AS stale FROM vessel_current_status",
        [],
    )


def daily_range_start(until: datetime, days: int) -> datetime:
    """Resolve the start of a trailing N-day range ending at ``until``.

    Same naive-UTC contract as :func:`window_start` -- ``until`` is expected
    to be one of :func:`reference_times_sql`'s tz-aware anchors, and tzinfo
    is stripped here so the result binds cleanly against ``order_test``'s
    naive timestamp columns the same way every other window in this module
    does.

    Parameters
    ----------
    until : datetime
        The simulated "now" the range ends at (inclusive of its own day).
    days : int
        Number of calendar days the range should cover, ``until``'s day
        included -- e.g. ``days=14`` with ``until`` on the 20th starts on
        the 7th.

    Returns
    -------
    datetime
        Naive UTC timestamp marking the start of the range.
    """
    return (until - timedelta(days=days - 1)).replace(tzinfo=None)


def daily_new_vessels_sql(since: datetime, until: datetime) -> tuple[str, list]:
    """Day-bucketed count of vessels first reported, one row per calendar day.

    Every day in ``[since, until]`` appears even with a zero count (via
    ``generate_series``), so the summary panel's trend chart never has to
    guess at a gap. Backed by ``vessel_current_status.first_date_received``,
    same "new vessel" definition :func:`new_vessels_sql` uses.

    Parameters
    ----------
    since : datetime
        Range start, from :func:`daily_range_start`.
    until : datetime
        The simulated "now" itself -- the same value ``since`` was computed
        from, so the last bucket is simulated-today.

    Returns
    -------
    tuple
        ``(sql, params)``; rows have ``day`` (date) and ``count`` (int).
    """
    return (
        (
            "WITH days AS ("
            "  SELECT generate_series(date_trunc('day', $1::timestamp), date_trunc('day', $2::timestamp), interval '1 day')::date AS day"
            "), counts AS ("
            "  SELECT date_trunc('day', first_date_received)::date AS day, COUNT(*) AS n"
            "  FROM vessel_current_status"
            "  WHERE first_date_received >= $1 AND first_date_received < $2"
            "  GROUP BY 1"
            ") "
            "SELECT d.day, COALESCE(c.n, 0)::int AS count "
            "FROM days d LEFT JOIN counts c USING (day) "
            "ORDER BY d.day"
        ),
        [since, until],
    )


def daily_status_changes_sql(since: datetime, until: datetime) -> tuple[str, list]:
    """Day-bucketed count of FIXED/OPEN/ON SUBS transitions, one row per calendar day.

    Backed by ``vessel_status_history``, with ``prev_status`` recomputed
    via ``LAG`` ordered by ``open_date_start`` the same way
    :func:`vessel_status_changes_sql` does -- see its docstring for why
    ``status IS DISTINCT FROM prev_status`` is checked explicitly rather
    than assumed from adjacency alone (a reporting gap that returns to the
    same status isn't a change), and why a vessel's first-ever segment
    (``is_first_segment``) is excluded.

    Parameters
    ----------
    since : datetime
        Range start, from :func:`daily_range_start`.
    until : datetime
        The simulated "now" itself.

    Returns
    -------
    tuple
        ``(sql, params)``; rows have ``day`` (date) and ``count`` (int).
    """
    return (
        (
            "WITH days AS ("
            "  SELECT generate_series(date_trunc('day', $1::timestamp), date_trunc('day', $2::timestamp), interval '1 day')::date AS day"
            "), ordered AS ("
            "  SELECT *, LAG(status) OVER (PARTITION BY vessel_id ORDER BY open_date_start) AS prev_status"
            "  FROM vessel_status_history"
            "), counts AS ("
            "  SELECT date_trunc('day', update_date)::date AS day, COUNT(*) AS n"
            "  FROM ordered"
            "  WHERE update_date >= $1 AND update_date < $2 AND NOT is_first_segment"
            "  AND status IS DISTINCT FROM prev_status"
            "  GROUP BY 1"
            ") "
            "SELECT d.day, COALESCE(c.n, 0)::int AS count "
            "FROM days d LEFT JOIN counts c USING (day) "
            "ORDER BY d.day"
        ),
        [since, until],
    )


def daily_new_orders_sql(since: datetime, until: datetime) -> tuple[str, list]:
    """Day-bucketed count of orders first received, one row per calendar day.

    Backed by ``public.order_test`` directly (no view covers orders), same
    "new order" definition :func:`new_orders_sql` uses.

    Parameters
    ----------
    since : datetime
        Range start, from :func:`daily_range_start`.
    until : datetime
        The simulated "now" itself.

    Returns
    -------
    tuple
        ``(sql, params)``; rows have ``day`` (date) and ``count`` (int).
    """
    return (
        (
            "WITH days AS ("
            "  SELECT generate_series(date_trunc('day', $1::timestamp), date_trunc('day', $2::timestamp), interval '1 day')::date AS day"
            "), counts AS ("
            "  SELECT date_trunc('day', date_received)::date AS day, COUNT(*) AS n"
            "  FROM public.order_test"
            "  WHERE date_received >= $1 AND date_received < $2"
            "  GROUP BY 1"
            ") "
            "SELECT d.day, COALESCE(c.n, 0)::int AS count "
            "FROM days d LEFT JOIN counts c USING (day) "
            "ORDER BY d.day"
        ),
        [since, until],
    )


# The three functions below are the per-region companions to
# daily_new_vessels_sql / daily_status_changes_sql / daily_new_orders_sql
# above -- same day range, but one row per (day, region) instead of one row
# per day, so the Daily Trends chart's hover tooltip can show a region
# breakdown alongside the bar's total. Deliberately NOT the source of the
# bar's own height: a vessel or order spanning multiple regions is counted
# once per region here (same convention regional_supply_demand already
# uses for its own supply/demand aggregation), so summing these rows for a
# day can exceed that day's total count -- that's expected, not a bug, and
# the two are combined at the application layer (see dashboard.py's
# daily_counts()), not unioned in SQL.


def daily_new_vessels_by_region_sql(since: datetime, until: datetime) -> tuple[str, list]:
    """Per-region breakdown of new vessels, one row per (day, region).

    Parameters
    ----------
    since : datetime
        Range start, from :func:`daily_range_start`.
    until : datetime
        The simulated "now" itself.

    Returns
    -------
    tuple
        ``(sql, params)``; rows have ``day`` (date), ``region`` (str), and
        ``count`` (int), ordered by day then descending count.
    """
    return (
        (
            "SELECT date_trunc('day', first_date_received)::date AS day, "
            "trim(zone) AS region, COUNT(*) AS count "
            "FROM vessel_current_status, LATERAL unnest(parent_zones) AS zone "
            "WHERE first_date_received >= $1 AND first_date_received < $2 "
            "AND trim(zone) <> '' "
            "GROUP BY 1, 2 ORDER BY 1, 3 DESC"
        ),
        [since, until],
    )


def daily_status_changes_by_region_sql(since: datetime, until: datetime) -> tuple[str, list]:
    """Per-region breakdown of FIXED/OPEN/ON SUBS transitions, one row per (day, region).

    Parameters
    ----------
    since : datetime
        Range start, from :func:`daily_range_start`.
    until : datetime
        The simulated "now" itself.

    Returns
    -------
    tuple
        ``(sql, params)``; rows have ``day`` (date), ``region`` (str), and
        ``count`` (int), ordered by day then descending count.
    """
    return (
        (
            "WITH ordered AS ("
            "  SELECT *, LAG(status) OVER (PARTITION BY vessel_id ORDER BY open_date_start) AS prev_status"
            "  FROM vessel_status_history"
            ") "
            "SELECT date_trunc('day', update_date)::date AS day, "
            "trim(zone) AS region, COUNT(*) AS count "
            "FROM ordered, "
            "LATERAL regexp_split_to_table(trim(COALESCE(parent_zone, '')), '\\s*,\\s*') AS zone "
            "WHERE update_date >= $1 AND update_date < $2 AND NOT is_first_segment "
            "AND status IS DISTINCT FROM prev_status "
            "AND trim(zone) <> '' "
            "GROUP BY 1, 2 ORDER BY 1, 3 DESC"
        ),
        [since, until],
    )


def daily_new_orders_by_region_sql(since: datetime, until: datetime) -> tuple[str, list]:
    """Per-region breakdown of new orders (by load zone), one row per (day, region).

    Parameters
    ----------
    since : datetime
        Range start, from :func:`daily_range_start`.
    until : datetime
        The simulated "now" itself.

    Returns
    -------
    tuple
        ``(sql, params)``; rows have ``day`` (date), ``region`` (str), and
        ``count`` (int), ordered by day then descending count.
    """
    return (
        (
            "SELECT date_trunc('day', date_received)::date AS day, "
            "trim(zone) AS region, COUNT(*) AS count "
            "FROM public.order_test, "
            "LATERAL regexp_split_to_table(trim(COALESCE(load_zone, '')), '\\s*,\\s*') AS zone "
            "WHERE date_received >= $1 AND date_received < $2 AND trim(zone) <> '' "
            "GROUP BY 1, 2 ORDER BY 1, 3 DESC"
        ),
        [since, until],
    )


def ecsa_ballasters_sql(sort: SortKey) -> tuple[str, list]:
    """ECSA ballast tracker list.

    Parameters
    ----------
    sort : "eta", "update_date", or "open_date_end"
        Whitelisted sort column.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    order_sql = _SORT_COLUMNS[sort]
    return f"SELECT * FROM ecsa_ballasters ORDER BY {order_sql}", []


def ecsa_history_sql(vessel_id: str) -> tuple[str, list]:
    """Resolved status timeline for one vessel (e.g. an ECSA ballaster).

    Each row is a (date range, status) segment -- see
    ``vessel_status_history`` in ``infra/sql/dashboard_gold_views.sql`` for
    how overlapping/disagreeing reports are reconciled into it. Ordered by
    ``open_date_start`` (chronological, oldest first) rather than
    ``update_date`` (when it was reported) -- a later report can describe
    an earlier segment than another row already on file, so only
    ``open_date_start`` order is guaranteed to read as a timeline.

    Parameters
    ----------
    vessel_id : str
        Vessel identifier, e.g. "VESSEL 0663".

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    return (
        "SELECT * FROM vessel_status_history WHERE vessel_id = $1 ORDER BY open_date_start",
        [vessel_id],
    )


def new_vessels_sql(since: datetime) -> tuple[str, list]:
    """Vessels first reported within the window.

    Parameters
    ----------
    since : datetime
        Window start, from :func:`window_start`.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    return (
        (
            "SELECT * FROM vessel_current_status "
            "WHERE first_date_received >= $1 "
            "ORDER BY first_date_received DESC"
        ),
        [since],
    )


def vessel_status_changes_sql(since: datetime) -> tuple[str, list]:
    """Status transitions (e.g. On Subs -> Open) within the window.

    ``vessel_status_history`` no longer stores ``prev_status`` directly, so
    it's recomputed here via ``LAG`` ordered by ``open_date_start`` -- the
    segment's own chronological position, not ``update_date``. Adjacent
    segments in that view are always a real status change *unless* there's
    a reporting gap between them (no row covered the days in between) that
    happens to return to the same status afterward -- e.g. OPEN, silence,
    then OPEN again is not a change even though it's two separate segments
    -- so ``status IS DISTINCT FROM prev_status`` is checked explicitly
    rather than assumed from the segment boundary alone.

    Also excludes a vessel's first-ever segment (``is_first_segment``) --
    that's a new arrival, reported separately by :func:`new_vessels_sql`,
    not a change (its ``prev_status`` is NULL, which is already "distinct"
    from anything, so this exclusion isn't implied by the check above).

    Parameters
    ----------
    since : datetime
        Window start.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    return (
        (
            "WITH ordered AS ("
            "  SELECT *, LAG(status) OVER (PARTITION BY vessel_id ORDER BY open_date_start) AS prev_status"
            "  FROM vessel_status_history"
            ") "
            "SELECT * FROM ordered "
            "WHERE update_date >= $1 AND NOT is_first_segment "
            "AND status IS DISTINCT FROM prev_status "
            "ORDER BY update_date DESC"
        ),
        [since],
    )


def vessels_no_longer_fresh_sql(since: datetime, window_len: timedelta) -> tuple[str, list]:
    """Vessels that reported inside the *previous* equivalent window but not this one.

    Presented in the UI as "Removed" per the product spec's acceptance
    criteria ("removed records are shown ... rather than silently
    disappearing"). Underneath, this is still only an inferred "went
    quiet" signal, not a real deletion -- nothing in this data model
    records a record being withdrawn. A vessel here had a report in
    [``since`` - ``window_len``, ``since``) but none in [``since``, now),
    so its current freshness state changed during this window even though
    its latest row is unchanged.

    Parameters
    ----------
    since : datetime
        Window start.
    window_len : timedelta
        Length of the window being compared (from :func:`window_length`), so
        the "previous window" comparison spans the same duration.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    previous_start = since - window_len
    return (
        (
            "SELECT v.* FROM vessel_current_status v "
            "WHERE v.update_date < $1 "
            "AND EXISTS ("
            "  SELECT 1 FROM public.tonnage_test t"
            "  WHERE t.vessel_id = v.vessel_id"
            "  AND t.update_date >= $2 AND t.update_date < $1"
            ") "
            "ORDER BY v.update_date DESC"
        ),
        [since, previous_start],
    )


# Fields compared row-to-row for the change feed's field-level diff.
# commercial_status is deliberately excluded -- that transition already has
# its own dedicated category (vessel_status_changes_sql /
# vessel_status_history), so including it here would report the same event
# twice under two different names.
_FIELD_CHANGE_COLUMNS = ("open_area", "dwt", "destination", "eta", "parent_zone", "ballast_laden")


def vessel_field_changes_sql(since: datetime, until: datetime) -> tuple[str, list]:
    """Per-vessel field-level changes within the window (not just status).

    For each vessel report in the window, compares it against that same
    vessel's immediately preceding (non-future) report and returns rows
    where at least one of :data:`_FIELD_CHANGE_COLUMNS` differs -- both the
    old and new value of every tracked column, so the caller can work out
    exactly which field(s) changed and show a field-level diff, per the
    product spec's "changed fields within an existing record are
    highlighted at the field level" acceptance criterion. A vessel's
    first-ever report is excluded (``prev_update_date IS NOT NULL``) --
    that's a new arrival, reported separately by :func:`new_vessels_sql`,
    not a change.

    Parameters
    ----------
    since : datetime
        Window start.
    until : datetime
        The simulated "now" itself -- both the row being compared and the
        row it's compared against must predate this, consistent with every
        other window in this module.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    tracked = ", ".join(
        f"{col}, LAG({col}) OVER w AS prev_{col}" for col in _FIELD_CHANGE_COLUMNS
    )
    diff_clause = " OR ".join(
        f"{col} IS DISTINCT FROM prev_{col}" for col in _FIELD_CHANGE_COLUMNS
    )
    sql = (
        "WITH windowed AS ("
        f"  SELECT vessel_id, update_date, {tracked}, "
        "    LAG(update_date) OVER w AS prev_update_date "
        "  FROM public.tonnage_test "
        "  WHERE update_date IS NOT NULL AND update_date < $2 "
        "  WINDOW w AS (PARTITION BY vessel_id ORDER BY update_date)"
        ") "
        "SELECT * FROM windowed "
        "WHERE update_date >= $1 "
        "AND prev_update_date IS NOT NULL "
        f"AND ({diff_clause}) "
        "ORDER BY update_date DESC"
    )
    return sql, [since, until]


def new_orders_sql(since: datetime, until: datetime) -> tuple[str, list]:
    """Orders first received within the window.

    Queries ``public.order_test`` directly rather than through a view, so
    ``until`` (the same simulated "now" ``since`` was computed from -- see
    :func:`reference_times_sql`) has to be applied here explicitly: an
    order dated on or after it is simulated-future and must be excluded
    outright, not just left outside the trailing window.

    Parameters
    ----------
    since : datetime
        Window start.
    until : datetime
        The simulated "now" itself -- orders on or after this are excluded.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    columns = ", ".join(_ORDERS_COLUMNS_SAFE)
    return (
        (
            f"SELECT {columns} FROM public.order_test "
            "WHERE date_received >= $1 AND date_received < $2 "
            "ORDER BY date_received DESC"
        ),
        [since, until],
    )


def amended_orders_sql(since: datetime, until: datetime) -> tuple[str, list]:
    """Orders revised within the window (updated after their initial receipt).

    ``public.order_test`` has no status column to track (the source
    glossary's "Commercial Status" field for orders isn't carried into
    this table by ``scripts/glue_transform.py``'s ``transform_orders``,
    confirmed live), so "changed" here can only mean "amended since first
    received", not a status transition. See :func:`new_orders_sql` for why
    ``until`` is needed here too.

    Parameters
    ----------
    since : datetime
        Window start.
    until : datetime
        The simulated "now" itself -- amendments on or after this are
        excluded.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    columns = ", ".join(_ORDERS_COLUMNS_SAFE)
    return (
        (
            f"SELECT {columns} FROM public.order_test "
            "WHERE update_date >= $1 AND update_date < $2 "
            "AND update_date IS DISTINCT FROM date_received "
            "ORDER BY update_date DESC"
        ),
        [since, until],
    )
