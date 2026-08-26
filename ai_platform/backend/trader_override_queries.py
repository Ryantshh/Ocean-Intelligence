"""Parameterised SQL for the Trader Status Override endpoints.

Reads/writes ``public.vessel_status_overrides`` (see
``infra/sql/trader_override_setup.sql``, which must be applied to the
database once before these queries will resolve) plus ``public.tonnage_test``
directly, read-only, for the vessel picker.

Deliberately does not compute or expose any derived "effective status" or
"open/closed" state from these overrides -- that's out of scope here and
tracked separately on the Supabase side (see the setup SQL's header). This
module only stores what a trader entered and reads it back.

Every function returns ``(sql, params)`` for
``ai_platform.backend.db.fetch_rows``, same convention as
``ai_platform.backend.dashboard_queries``. Column names are never taken from
a caller -- only values are, and always as bound parameters.
"""

from __future__ import annotations

from typing import Literal

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

    Reads ``public.tonnage_test`` directly -- the same source
    ``dashboard_queries.vessels_sql`` and the gold-loader keep current (see
    that module's header for why ``tonnage_test`` over the legacy
    ``tonnage``). Only ``vessel_id`` and raw ``commercial_status`` are
    returned; there is no join to ``vessel_status_overrides`` here, since
    this endpoint exists purely to populate the override form's vessel
    dropdown, not to show any override-derived state.

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


def vessel_history_sql(vessel_id: str) -> tuple[str, list]:
    """Every reported record for one vessel, newest first.

    ``vessel_id`` is not unique in ``tonnage_test`` -- it's a position-report
    log, not one-row-per-vessel (``scripts/glue_transform.py`` dedupes on
    ``(vessel_id, open_date_start, open_date_end, first_date_received)``, see
    ``dashboard_gold_views.sql``'s ``vessel_current_status`` view), so a
    vessel can carry several distinct rows over time. All of them are
    returned here, not just the latest, so the trader can see the vessel's
    reporting history before deciding what to override -- the first row is
    the current/latest one.

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
            "SELECT vessel_id, update_date, parent_zone, vessel_status, dwt, "
            "commercial_status, ship_type, ship_size, ballast_laden, destination, "
            "open_area, eta, open_date_start, open_date_end, first_date_received, "
            "order_id::text AS order_id "
            "FROM public.tonnage_test WHERE vessel_id = $1 "
            "ORDER BY update_date DESC NULLS LAST"
        ),
        [vessel_id],
    )


# Columns every insert/select below lists explicitly, in this order --
# override_status is the only one that's NOT NULL; the rest mirror a
# broker/trader "open" report and are optional, since a trader may only have
# fresh word on one or two fields, not a full re-report.
_OVERRIDE_COLUMNS = (
    "id", "vessel_id", "override_status", "open_area", "destination", "eta",
    "open_date_start", "open_date_end", "ballast_laden", "parent_zone",
    "entered_by", "note", "created_at",
)


def insert_override_sql(
    vessel_id: str,
    override_status: OverrideStatus,
    entered_by: str,
    note: str | None,
    *,
    open_area: str | None = None,
    destination: str | None = None,
    eta: str | None = None,
    open_date_start: str | None = None,
    open_date_end: str | None = None,
    ballast_laden: Literal["LADEN", "BALLAST"] | None = None,
    parent_zone: str | None = None,
) -> tuple[str, list]:
    """Record one trader-entered override.

    Append-only -- never updates or deletes an existing row, so this is
    always an INSERT. See ``infra/sql/trader_override_setup.sql`` for why:
    a vessel's "current" override is just its latest row by ``created_at``,
    and the full row history doubles as the audit trail.

    Parameters
    ----------
    vessel_id : str
        Vessel identifier, already confirmed to exist via
        :func:`vessel_exists_sql`.
    override_status : OverrideStatus
        One of the 12 values in :data:`OVERRIDE_STATUSES`, already validated
        by the API layer's Pydantic model before this is called.
    entered_by : str
        Free-text trader identifier -- the dashboard route has no login.
    note : str or None
        Optional free-text reason/context.
    open_area, destination, eta, open_date_start, open_date_end,
    ballast_laden, parent_zone : optional
        The rest of a broker-style "open" report -- all optional, mirroring
        the corresponding ``tonnage_test`` columns.

    Returns
    -------
    tuple
        ``(sql, params)``; returns the inserted row.
    """
    columns = (
        "vessel_id, override_status, open_area, destination, eta, "
        "open_date_start, open_date_end, ballast_laden, parent_zone, "
        "entered_by, note"
    )
    returning = ", ".join(_OVERRIDE_COLUMNS)
    # eta/open_date_start/open_date_end arrive as plain "YYYY-MM-DD" strings
    # from an HTML <input type="date">; asyncpg binds a bare str against a
    # `timestamp` column parameter type mismatch, so these are cast in SQL
    # (text -> timestamp) rather than parsed into datetime objects in Python.
    return (
        (
            f"INSERT INTO public.vessel_status_overrides ({columns}) "
            "VALUES ($1, $2, $3, $4, $5::timestamp, $6::timestamp, $7::timestamp, "
            "$8, $9, $10, $11) "
            f"RETURNING {returning}"
        ),
        [
            vessel_id, override_status, open_area, destination, eta,
            open_date_start, open_date_end, ballast_laden, parent_zone,
            entered_by, note,
        ],
    )


def audit_sql(vessel_id: str | None, limit: int) -> tuple[str, list]:
    """Full override history, newest first.

    Deliberately unfiltered by anything except an optional exact
    ``vessel_id`` match -- every override a trader ever entered stays
    visible here regardless of what it did (or, currently, didn't do) to any
    computed open/closed state elsewhere, per the acceptance criterion that
    overridden vessels "remain visible in status-change and audit views."

    Parameters
    ----------
    vessel_id : str or None
        Restrict to one vessel's history, or None for every vessel.
    limit : int
        Maximum rows to return.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    columns = ", ".join(_OVERRIDE_COLUMNS)
    if vessel_id is not None:
        return (
            f"SELECT {columns} FROM public.vessel_status_overrides "
            "WHERE vessel_id = $1 "
            "ORDER BY created_at DESC "
            "LIMIT $2",
            [vessel_id, limit],
        )
    return (
        f"SELECT {columns} FROM public.vessel_status_overrides "
        "ORDER BY created_at DESC "
        "LIMIT $1",
        [limit],
    )
