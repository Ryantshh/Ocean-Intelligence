"""Parameterised SQL for the Trader Status Override endpoints.

Reads and writes ``public.tonnage_test`` directly -- the same gold table
the Dashboard tab's own views (``infra/sql/dashboard_gold_views.sql``) are
built from, but queried/written here directly rather than through
``vessel_current_status``. A trader's submission replaces one existing
``tonnage_test`` row rather than landing in a separate overrides table:
see :func:`override_tonnage_row_sql` for the copy-edit-swap this actually
does. Either way the row lands directly in ``tonnage_test``, so the
Dashboard tab's ``vessel_current_status`` view -- which always takes the
newest ``tonnage_test`` row per vessel -- picks the change up automatically
with no dashboard-side code change.

Also writes (never reads, outside :func:`audit_sql`) to
``public.trader_override_audit`` (see
``ai_platform/trader_override/trader_override_audit_setup.sql``, which
must be applied to the database once before :func:`audit_sql`/
:func:`insert_audit_sql` will resolve) -- purely a log of what was
submitted and by whom, since ``tonnage_test`` itself has no column marking
a row as trader-entered and so cannot answer "trader overrides only" on
its own. Never the source of truth for a vessel's status, and never joined
into anything :func:`vessels_sql`/:func:`vessel_history_sql` return.

Every function returns ``(sql, params)`` for
``ai_platform.backend.db.fetch_rows``, same convention as
``ai_platform.backend.dashboard_queries``. Column names are never taken from
a caller -- only values are, and always as bound parameters.
"""

from __future__ import annotations

import secrets
from datetime import date
from typing import Literal


def _parse_date(value: str | None) -> date | None:
    """Turn a "YYYY-MM-DD" string (from an HTML ``<input type="date">``)
    into a ``date`` asyncpg will accept for a ``timestamp`` parameter.

    asyncpg infers a bound parameter's type from the query itself -- a
    ``$n::timestamp`` cast makes it require an actual ``date``/``datetime``
    instance, not a string, and rejects a plain ``str`` even though
    Postgres itself would happily cast one. Parsing here, once, keeps every
    call site plain.
    """
    return date.fromisoformat(value) if value else None

OverrideStatus = Literal[
    "AVAILABLE",
    "ON SUBS",
    "FIXED",
    "FAILED",
    "CANCELLED",
    "POSS FIXED",
    "PROGRAM",
    "CONTRACT",
    "RELET",
    "BALLAST FIXED",
    "DO NOT COUNT",
    "WATCHLIST",
]

OVERRIDE_STATUSES: tuple[OverrideStatus, ...] = (
    "AVAILABLE",
    "ON SUBS",
    "FIXED",
    "FAILED",
    "CANCELLED",
    "POSS FIXED",
    "PROGRAM",
    "CONTRACT",
    "RELET",
    "BALLAST FIXED",
    "DO NOT COUNT",
    "WATCHLIST",
)


def vessels_sql() -> tuple[str, list]:
    """Vessel picker: one row per vessel, its latest reported raw status.

    Reads ``public.tonnage_test`` directly -- deliberately not
    ``vessel_current_status`` (the Dashboard tab's own view): this table
    is queried here as-is, with no simulated-"now" freshness cutoff and no
    FIXED/OPEN/ON SUBS window-containment computation, so a trader always
    sees literally the latest row on file per vessel, independent of
    whatever the Dashboard tab currently shows for it. Only ``vessel_id``
    and raw ``commercial_status`` are returned; this endpoint exists purely
    to populate the override form's vessel dropdown.

    Returns
    -------
    tuple
        ``(sql, params)``; one row per vessel, most recently updated first.
    """
    return (
        (
            "SELECT DISTINCT ON (vessel_id) vessel_id, commercial_status, update_date "
            "FROM public.tonnage_test "
            "ORDER BY vessel_id, update_date DESC NULLS LAST"
        ),
        [],
    )


def vessel_exists_sql(vessel_id: str) -> tuple[str, list]:
    """Check whether a vessel_id is a real vessel in the gold tonnage source.

    Reads ``public.tonnage_test`` directly, same as :func:`vessels_sql` --
    not gated by ``vessel_current_status``'s freshness cutoff, so a vessel
    with only simulated-future reports on file (invisible on the Dashboard
    tab) can still be overridden here.

    Parameters
    ----------
    vessel_id : str
        Vessel identifier submitted with an override request.

    Returns
    -------
    tuple
        ``(sql, params)``; a non-empty result means the vessel exists.
    """
    return (
        "SELECT 1 FROM public.tonnage_test WHERE vessel_id = $1 LIMIT 1",
        [vessel_id],
    )


def tonnage_row_snapshot_sql(base_tonnage_row_key: str, vessel_id: str) -> tuple[str, list]:
    """The trader-editable fields of one existing row, before it's overridden.

    Read right before :func:`override_tonnage_row_sql` swaps that row out,
    so :func:`insert_audit_sql` can log what each field actually changed
    *from* -- ``trader_override_audit`` otherwise only knows what was
    submitted, not what was replaced. Also doubles as the "does this row
    still exist for this vessel" check the API layer 404s on, ahead of
    attempting the swap itself.

    Parameters
    ----------
    base_tonnage_row_key : str
        ``tonnage_row_key`` of the row about to be overridden.
    vessel_id : str
        Vessel identifier, checked alongside the key so a stale/mismatched
        key can never read a different vessel's row.

    Returns
    -------
    tuple
        ``(sql, params)``; empty result means the row no longer exists (or
        never did) for that vessel.
    """
    return (
        (
            "SELECT commercial_status, open_area, open_date_start, open_date_end, "
            "order_id::text AS order_id "
            "FROM public.tonnage_test WHERE tonnage_row_key = $1 AND vessel_id = $2"
        ),
        [base_tonnage_row_key, vessel_id],
    )


def vessel_history_sql(vessel_id: str) -> tuple[str, list]:
    """Every reported record for one vessel, newest first.

    ``vessel_id`` is not unique in ``tonnage_test`` -- it's a position-report
    log, not one-row-per-vessel (``scripts/glue_transform.py`` dedupes on
    ``(vessel_id, open_date_start, open_date_end, first_date_received)``, see
    ``dashboard_gold_views.sql``'s ``vessel_current_status`` view), so a
    vessel can carry several distinct rows over time. All of them are
    returned here, not just the latest, so the trader can see the vessel's
    reporting history before deciding what to override -- the first row is
    the current/latest one. A trader's own past submissions appear in this
    same history like any other row, since :func:`override_tonnage_row_sql`
    writes into this same table (as a replacement for whichever row was
    chosen as the baseline, not a plain append).

    Every non-embedding, non-pipeline-internal column ``tonnage_test`` has --
    including fields this feature doesn't let a trader edit (``vessel_status``,
    ``dwt``, ``ship_type``, ``ship_size``) so they know why those are absent
    from the override form.

    Parameters
    ----------
    vessel_id : str
        Vessel identifier.

    Returns
    -------
    tuple
        ``(sql, params)``; empty result means the vessel doesn't exist. Rows
        ordered newest-first by ``update_date``.
    """
    return (
        (
            "SELECT tonnage_row_key, vessel_id, update_date, parent_zone, vessel_status, dwt, "
            "commercial_status, ship_type, ship_size, ballast_laden, destination, "
            "open_area, eta, open_date_start, open_date_end, first_date_received, "
            "order_id::text AS order_id "
            "FROM public.tonnage_test WHERE vessel_id = $1 "
            "ORDER BY update_date DESC NULLS LAST"
        ),
        [vessel_id],
    )


def override_tonnage_row_sql(
    base_tonnage_row_key: str,
    vessel_id: str,
    commercial_status: OverrideStatus,
    *,
    open_area: str | None = None,
    open_date_start: str | None = None,
    open_date_end: str | None = None,
    order_assignment: str | None = None,
) -> tuple[str, list]:
    """Replace one ``tonnage_test`` row with a copy carrying the trader's edits.

    Not append-only: ``base_tonnage_row_key`` (from a row
    :func:`vessel_history_sql` returned -- the trader's override baseline,
    the vessel's newest row by default, or an older one if they clicked a
    different row in Current Record) identifies exactly one existing row.
    This copies every column of that row, overwrites only the columns the
    trader actually submitted a value for, and atomically inserts the
    result as a new row and deletes the base row -- one CTE-chained
    statement so a partial failure can't leave both or neither on file.
    Every field this feature doesn't let a trader touch at all
    (``parent_zone``, ``vessel_status``, ``dwt``, ``ship_type``,
    ``ship_size``, ``ballast_laden``, ``destination``, ``eta``, the four
    ``*_embedding`` columns, ``embedding_source_hash``, ``gold_loaded_at``)
    is copied from the base row unchanged. A field the trader *can* touch
    but left blank this submission (``open_area``, the open dates,
    ``order_assignment``) is also copied from the base row rather than
    cleared -- only ``commercial_status`` is always the trader's value,
    since the form requires it.

    Carrying the embeddings/hash forward verbatim means they can go stale
    against a changed ``open_area``/``destination`` until the real Shipfix
    pipeline next overwrites this vessel -- accepted here on the trader's
    own instruction to preserve every untouched field's data rather than
    null it out (the previous append-only design's tradeoff was the
    opposite: always ``NULL``, never stale).

    ``update_date`` is stamped with ``now() - interval '1 year'`` --
    duplicating, not calling, ``dashboard_gold_views.sql``'s
    ``tonnage_reference_now()`` so this module has no runtime dependency on
    dashboard-side SQL. The real clock is simulated-*future* under that
    convention (see the gold views' header) and ``vessel_current_status``
    excludes any row dated on or after it outright -- a row stamped with
    the real ``now()`` would never appear on the Dashboard tab at all. Keep
    this expression in sync with ``tonnage_reference_now()`` by hand if
    that convention ever changes. ``first_date_received`` is carried over
    from the base row unchanged -- it's still the same position, first
    reported whenever it originally was; only ``update_date`` reflects this
    edit.

    A new ``tonnage_row_key`` (``tonnage_test``'s primary key) is generated
    here rather than left to a default, since the column has none.
    ``order_assignment`` is written straight into ``order_id`` -- the only
    ``tonnage_test`` column an "order assignment" could mean -- so, like
    every other field here, it can be overwritten by the next real Shipfix
    report for the vessel; there is no override table protecting it.

    Parameters
    ----------
    base_tonnage_row_key : str
        ``tonnage_row_key`` of the existing row being replaced.
    vessel_id : str
        Vessel identifier, already confirmed to exist via
        :func:`vessel_exists_sql`. Also checked against the base row itself
        so a stale/mismatched key can never edit a different vessel.
    commercial_status : OverrideStatus
        One of the 12 values in :data:`OVERRIDE_STATUSES`, already
        validated by the API layer's Pydantic model before this is called.
        Only ``FIXED``/``ON SUBS`` are recognised by
        ``vessel_current_status``'s booking-window logic -- every other
        value is still written here, but reads as OPEN on the Dashboard
        tab.
    open_area, open_date_start, open_date_end, order_assignment : optional
        The rest of what a trader might have fresh word on -- all
        optional, since a trader may only have new word on the status, not
        a full re-report; whatever's left blank falls back to the base
        row's own value for that column.

    Returns
    -------
    tuple
        ``(sql, params)``; returns the newly-inserted row, or no rows if
        ``base_tonnage_row_key`` doesn't match an existing row for
        ``vessel_id`` (the base row was deleted/edited concurrently, or the
        key is stale) -- the API layer treats that as a 404.
    """
    new_tonnage_row_key = secrets.token_hex(32)
    # open_date_start/open_date_end arrive as plain "YYYY-MM-DD" strings from
    # an HTML <input type="date">; parsed to date objects via _parse_date so
    # asyncpg's own type inference on the $6/$7::timestamp casts (which
    # requires a real date/datetime, not a str) accepts them.
    # order_assignment stays a str, cast text -> bigint in SQL; a non-numeric
    # value surfaces as a plain database error (502), same convention as
    # everywhere else in this module.
    return (
        (
            "WITH base AS ("
            "  SELECT * FROM public.tonnage_test"
            "  WHERE tonnage_row_key = $1 AND vessel_id = $2"
            "), removed AS ("
            "  DELETE FROM public.tonnage_test"
            "  WHERE tonnage_row_key = $1 AND vessel_id = $2"
            "  RETURNING tonnage_row_key"
            ") "
            "INSERT INTO public.tonnage_test ("
            "  tonnage_row_key, vessel_id, update_date, commercial_status, open_area,"
            "  open_date_start, open_date_end, order_id, first_date_received,"
            "  parent_zone, vessel_status, dwt, ship_type, ship_size, ballast_laden,"
            "  destination, eta, vessel_status_embedding, destination_embedding,"
            "  parent_zone_embedding, open_area_embedding, embedding_source_hash, gold_loaded_at"
            ") "
            "SELECT"
            "  $3, base.vessel_id, now() - interval '1 year', $4,"
            "  COALESCE($5, base.open_area),"
            "  COALESCE($6::timestamp, base.open_date_start),"
            "  COALESCE($7::timestamp, base.open_date_end),"
            "  COALESCE($8::text::bigint, base.order_id),"
            "  base.first_date_received,"
            "  base.parent_zone, base.vessel_status, base.dwt, base.ship_type, base.ship_size,"
            "  base.ballast_laden, base.destination, base.eta,"
            "  base.vessel_status_embedding, base.destination_embedding,"
            "  base.parent_zone_embedding, base.open_area_embedding,"
            "  base.embedding_source_hash, base.gold_loaded_at "
            "FROM base CROSS JOIN removed "
            "RETURNING vessel_id, update_date, commercial_status, open_area, "
            "open_date_start, open_date_end, order_id::text AS order_id"
        ),
        [
            base_tonnage_row_key, vessel_id, new_tonnage_row_key, commercial_status,
            open_area, _parse_date(open_date_start), _parse_date(open_date_end), order_assignment,
        ],
    )


# Columns every audit insert/select below lists explicitly, in this order.
# The old_* columns are nullable -- rows logged before this column set
# existed have them NULL, meaning "unknown before-value", not "unchanged".
_AUDIT_COLUMNS = (
    "id", "vessel_id",
    "old_override_status", "override_status",
    "old_open_area", "open_area",
    "old_open_date_start", "open_date_start",
    "old_open_date_end", "open_date_end",
    "old_order_assignment", "order_assignment",
    "entered_by", "created_at",
)


def insert_audit_sql(
    vessel_id: str,
    override_status: OverrideStatus,
    entered_by: str,
    *,
    open_area: str | None = None,
    open_date_start: object = None,
    open_date_end: object = None,
    order_assignment: str | None = None,
    old_override_status: str | None = None,
    old_open_area: str | None = None,
    old_open_date_start: object = None,
    old_open_date_end: object = None,
    old_order_assignment: str | None = None,
) -> tuple[str, list]:
    """Log one trader submission to ``trader_override_audit``, before and after.

    Called right after :func:`override_tonnage_row_sql` succeeds, with both
    sides pulled from actual ``tonnage_test`` state rather than the raw
    request body: the new-value parameters are that call's own
    ``RETURNING`` row (the *effective* values actually written -- a field
    the trader left blank resolves to the base row's own value there via
    ``COALESCE``, not to blank), and the ``old_*`` parameters are the same
    row's values just *before* the swap, from
    :func:`tonnage_row_snapshot_sql`. Logging the request body's raw
    (possibly blank) fields as "new" here would make an untouched field
    look like it was cleared, when the swap actually carried its old value
    forward. This table itself is still append-only (a full trail of every
    submission ever made), unlike ``tonnage_test`` where each submission
    now replaces its baseline row.

    Parameters
    ----------
    vessel_id : str
        Vessel identifier.
    override_status : OverrideStatus
        The vessel's resulting status (``override_tonnage_row_sql``'s
        ``RETURNING`` row -- always the trader's submitted value, since the
        form requires it).
    entered_by : str
        Free-text trader identifier -- the dashboard route has no login.
    open_area, order_assignment : optional
        The vessel's resulting values for those columns, from the same
        ``RETURNING`` row -- already plain text, no parsing needed here.
    open_date_start, open_date_end : optional
        Same, but ``date``/``datetime`` objects (asyncpg's own decoding of
        a ``timestamp`` column, same as the ``RETURNING`` row yields).
    old_override_status, old_open_area, old_order_assignment : optional
        The overridden row's own values for those columns, straight from
        :func:`tonnage_row_snapshot_sql` -- already plain text, no parsing
        needed here.
    old_open_date_start, old_open_date_end : optional
        Same, but ``date``/``datetime`` objects, same source.

    Returns
    -------
    tuple
        ``(sql, params)``; returns the inserted row.
    """
    columns = (
        "vessel_id, old_override_status, override_status, "
        "old_open_area, open_area, "
        "old_open_date_start, open_date_start, "
        "old_open_date_end, open_date_end, "
        "old_order_assignment, order_assignment, entered_by"
    )
    returning = ", ".join(_AUDIT_COLUMNS)
    return (
        (
            f"INSERT INTO public.trader_override_audit ({columns}) "
            "VALUES ($1, $2, $3, $4, $5, $6::timestamp, $7::timestamp, "
            "$8::timestamp, $9::timestamp, $10, $11, $12) "
            f"RETURNING {returning}"
        ),
        [
            vessel_id, old_override_status, override_status,
            old_open_area, open_area,
            old_open_date_start, open_date_start,
            old_open_date_end, open_date_end,
            old_order_assignment, order_assignment, entered_by,
        ],
    )


def audit_sql(vessel_id: str | None, limit: int) -> tuple[str, list]:
    """Trader override audit trail, newest first.

    Reads ``trader_override_audit`` only -- never ``tonnage_test`` -- so
    this is scoped strictly to submissions made through this form, not
    every position report on file.

    Parameters
    ----------
    vessel_id : str or None
        Restrict to one vessel's submissions, or None for every vessel.
    limit : int
        Maximum rows to return.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    columns = ", ".join(_AUDIT_COLUMNS)
    if vessel_id is not None:
        return (
            f"SELECT {columns} FROM public.trader_override_audit "
            "WHERE vessel_id = $1 "
            "ORDER BY created_at DESC "
            "LIMIT $2",
            [vessel_id, limit],
        )
    return (
        f"SELECT {columns} FROM public.trader_override_audit "
        "ORDER BY created_at DESC "
        "LIMIT $1",
        [limit],
    )
