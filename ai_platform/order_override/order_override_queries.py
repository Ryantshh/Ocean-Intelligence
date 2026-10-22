"""Parameterised SQL for the Order Status Override endpoints.

Reads and writes ``public.order_test`` directly. An order is identified by
``(order_id, order_seq)``, the table's primary key, never ``order_id``
alone: ``order_id`` is a hash of ten content fields, so orders identical in
all ten share one and are numbered apart by ``order_seq`` (1, 2, ...).
Whether such twins are one order recorded twice or two real orders is for
the trader to judge, so each is read and updated on its own, and an update
to one never touches the other.

Unlike ``tonnage_test`` (a position-report log with several rows per
vessel), each ``(order_id, order_seq)`` is one row, so an override here is a
plain ``UPDATE`` in place, not the copy/delete/insert swap
:mod:`ai_platform.trader_override.trader_override_queries` uses for
tonnage. There is also no separate overrides table: the edited row lands
directly in ``order_test``, same convention as the tonnage side.

``laycan_start``/``laycan_end``/``cargo_weight_min``/``cargo_weight_max``
are written here -- the latter two standing in for "quantity", since
``order_test`` has no single quantity column, only these two.
``customer_name`` is deliberately not persisted anywhere yet --
``order_test`` has no matching column, and how to add one is an open
schema question, not something this module decides on its own.

Every function returns ``(sql, params)`` for
``ai_platform.backend.db.fetch_rows``, same convention as
``ai_platform.trader_override.trader_override_queries``. Column names are
never taken from a caller -- only values are, and always as bound
parameters.
"""

from __future__ import annotations

from datetime import date


def _parse_date(value: str | None) -> date | None:
    """Turn a "YYYY-MM-DD" string (from an HTML ``<input type="date">``)
    into a ``date`` asyncpg will accept for a ``timestamp`` parameter.

    Same reasoning as ``trader_override_queries._parse_date``: asyncpg
    infers a bound parameter's type from the query itself, and a
    ``$n::timestamp`` cast requires an actual ``date``/``datetime``
    instance, not a plain ``str``.
    """
    return date.fromisoformat(value) if value else None


def orders_sql() -> tuple[str, list]:
    """Order picker: every order, most recently updated first.

    Returns
    -------
    tuple
        ``(sql, params)``; one row per ``(order_id, order_seq)`` with just
        enough context (cargo type, ports, laycan window) to populate the
        override form's order search combobox, plus ``order_seq_count``,
        how many orders share that ``order_id``, so the picker can tell
        twins apart.
    """
    return (
        (
            "SELECT order_id::text AS order_id, order_seq, "
            "count(*) OVER (PARTITION BY order_id) AS order_seq_count, "
            "cargo_type, load_port, discharge_port, "
            "laycan_start, laycan_end, update_date "
            "FROM public.order_test "
            "ORDER BY update_date DESC NULLS LAST, order_id, order_seq"
        ),
        [],
    )


def order_detail_sql(order_id: str, order_seq: int) -> tuple[str, list]:
    """Every trader-relevant field of one order, for the Current Record panel.

    ``(order_id, order_seq)`` is the primary key, so this returns at most one
    row -- there is no reporting history to page through the way
    :func:`trader_override_queries.vessel_history_sql` does for a vessel.
    ``order_seq_count`` says how many orders share the ``order_id``, so the
    panel can tell the trader this one has twins.

    Parameters
    ----------
    order_id : str
        Order identifier, cast to ``bigint`` in SQL.
    order_seq : int
        Which of the orders sharing ``order_id`` this is.

    Returns
    -------
    tuple
        ``(sql, params)``; empty result means the order doesn't exist.
    """
    return (
        (
            "SELECT order_id::text AS order_id, order_seq, "
            "(SELECT count(*) FROM public.order_test t "
            "WHERE t.order_id = o.order_id) AS order_seq_count, "
            "cargo_type, cargo_description, "
            "load_port, load_zone, discharge_port, discharge_parent_zone, "
            "cargo_weight_min, cargo_weight_max, laycan_start, laycan_end, "
            "date_received, update_date, assigned "
            "FROM public.order_test o "
            "WHERE order_id = $1::text::bigint AND order_seq = $2"
        ),
        [order_id, order_seq],
    )


def order_records_sql(order_id: str) -> tuple[str, list]:
    """Every ``order_seq`` row sharing one ``order_id``, for the delete picker.

    Same columns as :func:`order_detail_sql`, ordered by ``order_seq``.
    """
    return (
        (
            "SELECT order_id::text AS order_id, order_seq, "
            "(SELECT count(*) FROM public.order_test t "
            "WHERE t.order_id = o.order_id) AS order_seq_count, "
            "cargo_type, cargo_description, "
            "load_port, load_zone, discharge_port, discharge_parent_zone, "
            "cargo_weight_min, cargo_weight_max, laycan_start, laycan_end, "
            "date_received, update_date, assigned "
            "FROM public.order_test o "
            "WHERE order_id = $1::text::bigint ORDER BY order_seq"
        ),
        [order_id],
    )


def update_order_sql(
    order_id: str,
    order_seq: int,
    *,
    laycan_start: str | None,
    laycan_end: str | None,
    cargo_weight_min: float | None,
    cargo_weight_max: float | None,
) -> tuple[str, list]:
    """Update one order's laycan window and cargo weight range in place.

    A field left blank keeps the order's existing value via ``COALESCE``,
    same convention as :func:`trader_override_queries.override_tonnage_row_sql`
    -- a trader may only have fresh word on one side of the window, not both.
    ``cargo_weight_min``/``cargo_weight_max`` stand in for "quantity" --
    ``order_test`` has no single quantity column, only these two (see the
    module docstring). ``update_date`` is stamped with
    ``now() - interval '1 year'``, duplicating (not calling)
    ``dashboard_gold_views.sql``'s ``orders_reference_now()`` so this
    module has no runtime dependency on dashboard-side SQL -- keep this
    expression in sync by hand if that convention ever changes.

    Parameters
    ----------
    order_id : str
        Order identifier, already confirmed to exist via
        :func:`order_detail_sql`.
    order_seq : int
        Which of the orders sharing ``order_id`` to update. The others are
        left alone: whether they are the same order is the trader's call,
        not something an update to one of them should decide.
    laycan_start, laycan_end : str or None
        "YYYY-MM-DD" strings from an HTML ``<input type="date">``, or
        ``None`` to leave that side of the window unchanged.
    cargo_weight_min, cargo_weight_max : float or None
        Already-parsed numbers (Pydantic validates the incoming JSON body
        at the API layer), or ``None`` to leave that side unchanged.

    Returns
    -------
    tuple
        ``(sql, params)``; returns the updated row, or no rows if the order
        no longer exists (deleted concurrently) -- the API layer treats
        that as a 404.
    """
    return (
        (
            "UPDATE public.order_test SET "
            "laycan_start = COALESCE($2::timestamp, laycan_start), "
            "laycan_end = COALESCE($3::timestamp, laycan_end), "
            "cargo_weight_min = COALESCE($4::numeric, cargo_weight_min), "
            "cargo_weight_max = COALESCE($5::numeric, cargo_weight_max), "
            "update_date = now() - interval '1 year' "
            "WHERE order_id = $1::text::bigint AND order_seq = $6 "
            "RETURNING order_id::text AS order_id, order_seq, cargo_type, load_port, discharge_port, "
            "laycan_start, laycan_end, cargo_weight_min, cargo_weight_max, update_date"
        ),
        [
            order_id, _parse_date(laycan_start), _parse_date(laycan_end),
            cargo_weight_min, cargo_weight_max, order_seq,
        ],
    )


def delete_order_sql(order_id: str, order_seq: int) -> tuple[str, list]:
    """Delete one ``(order_id, order_seq)`` row, returning its editable fields.

    ``RETURNING`` supplies the deleted values for the audit log's ``old_*``
    side. Empty result means the order no longer exists. Twins sharing
    ``order_id`` are left alone.
    """
    return (
        (
            "DELETE FROM public.order_test "
            "WHERE order_id = $1::text::bigint AND order_seq = $2 "
            "RETURNING laycan_start, laycan_end, cargo_weight_min, cargo_weight_max"
        ),
        [order_id, order_seq],
    )


# Columns every audit insert/select below lists explicitly, in this order.
_AUDIT_COLUMNS = (
    "id", "order_id", "order_seq",
    "old_laycan_start", "laycan_start",
    "old_laycan_end", "laycan_end",
    "old_cargo_weight_min", "cargo_weight_min",
    "old_cargo_weight_max", "cargo_weight_max",
    "customer_name", "entered_by", "created_at", "action", "reason",
)


def insert_audit_sql(
    order_id: str,
    order_seq: int,
    entered_by: str,
    *,
    laycan_start: object = None,
    laycan_end: object = None,
    cargo_weight_min: float | None = None,
    cargo_weight_max: float | None = None,
    customer_name: str | None = None,
    old_laycan_start: object = None,
    old_laycan_end: object = None,
    old_cargo_weight_min: float | None = None,
    old_cargo_weight_max: float | None = None,
    action: str = "UPDATE",
    reason: str | None = None,
) -> tuple[str, list]:
    """Log one trader submission to ``order_override_audit``, before and after.

    Called right after :func:`update_order_sql` succeeds, with both sides
    pulled from actual ``order_test`` state rather than the raw request
    body -- same reasoning as
    :func:`trader_override_queries.insert_audit_sql`: the new-value
    parameters are that call's own ``RETURNING`` row (the *effective*
    values actually written -- a field the trader left blank resolves to
    the order's own existing value there via ``COALESCE``, not to blank),
    and the ``old_*`` parameters are the same row's values just *before*
    the update. ``customer_name`` has no old/new distinction -- it isn't a
    column ``order_test`` has at all, so there is nothing to diff it
    against, only what was entered this submission.

    Parameters
    ----------
    order_id : str
        Order identifier.
    order_seq : int
        Which of the orders sharing ``order_id`` was updated.
    entered_by : str
        Free-text trader identifier -- the dashboard route has no login.
    laycan_start, laycan_end : date/datetime or None
        The order's resulting values for those columns, from
        :func:`update_order_sql`'s ``RETURNING`` row.
    cargo_weight_min, cargo_weight_max : float or None
        Same, already plain numbers -- no parsing needed.
    customer_name : str or None
        Whatever the trader typed this submission, logged as-is.
    old_laycan_start, old_laycan_end, old_cargo_weight_min, old_cargo_weight_max : optional
        The order's own values for those columns just before the update,
        from a read of :func:`order_detail_sql` taken beforehand.

    action : str
        ``UPDATE`` (default) or ``DELETE``; a delete logs the removed row's
        values on the ``old_*`` side and leaves the new side null.

    reason : str or None
        Why the trader made (or deleted) this record, logged as typed.

    Returns
    -------
    tuple
        ``(sql, params)``; returns the inserted row.
    """
    columns = (
        "order_id, order_seq, old_laycan_start, laycan_start, "
        "old_laycan_end, laycan_end, "
        "old_cargo_weight_min, cargo_weight_min, "
        "old_cargo_weight_max, cargo_weight_max, "
        "customer_name, entered_by, action, reason"
    )
    returning = ", ".join(_AUDIT_COLUMNS)
    return (
        (
            f"INSERT INTO public.order_override_audit ({columns}) "
            "VALUES ($1, $2, $3::timestamp, $4::timestamp, $5::timestamp, $6::timestamp, "
            "$7::numeric, $8::numeric, $9::numeric, $10::numeric, $11, $12, $13, $14) "
            f"RETURNING {returning}"
        ),
        [
            order_id, order_seq, old_laycan_start, laycan_start,
            old_laycan_end, laycan_end,
            old_cargo_weight_min, cargo_weight_min,
            old_cargo_weight_max, cargo_weight_max,
            customer_name, entered_by, action, reason,
        ],
    )


def audit_sql(order_id: str | None, limit: int) -> tuple[str, list]:
    """Order override audit trail, newest first.

    Reads ``order_override_audit`` only -- never ``order_test`` -- so this
    is scoped strictly to submissions made through this form, not every
    order on file.

    Parameters
    ----------
    order_id : str or None
        Restrict to submissions for that ``order_id`` -- every
        ``order_seq`` sharing it, each row saying which -- or None for
        every order.
    limit : int
        Maximum rows to return.

    Returns
    -------
    tuple
        ``(sql, params)``.
    """
    columns = ", ".join(_AUDIT_COLUMNS)
    if order_id is not None:
        return (
            (
                f"SELECT {columns} FROM public.order_override_audit "
                "WHERE order_id = $1 "
                "ORDER BY created_at DESC "
                "LIMIT $2"
            ),
            [order_id, limit],
        )
    return (
        (
            f"SELECT {columns} FROM public.order_override_audit "
            "ORDER BY created_at DESC "
            "LIMIT $1"
        ),
        [limit],
    )
