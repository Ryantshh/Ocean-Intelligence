"""Order Status Override endpoints -- the order-side counterpart to
:mod:`ai_platform.trader_override.trader_override`.

Lets a trader record fresh word on an order's laycan window and cargo
weight range before it reaches Shipfix/the pipeline. Writes straight into
``public.order_test`` by updating the matching row in place. An order is
``(order_id, order_seq)``, never ``order_id`` alone: orders identical in the
ten fields ``order_id`` is hashed from share it, numbered apart by
``order_seq``, and whether they are one order or two is the trader's call,
so every endpoint here names both. Each pair is one row (unlike
``tonnage_test``'s ``vessel_id``), so there is no copy/delete/insert swap to
do, and no separate overrides table.

``laycan_start``/``laycan_end``/``cargo_weight_min``/``cargo_weight_max``
are persisted -- the weight pair standing in for "quantity", since
``order_test`` has no single quantity column. ``customer_name`` isn't a
column ``order_test`` has -- adding one is an open schema question -- so
this module never attempts to store it; a submission that includes it is
logged (not persisted) purely so there's some trace of what a trader
entered until that's decided.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ai_platform.backend.db import fetch_rows
from ai_platform.backend.logging_utils import get_logger
from ai_platform.order_override import order_override_queries as ooq

router = APIRouter(prefix="/api/order-override", tags=["order-override"])
logger = get_logger("order_override")


async def _run(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Execute one query and surface failures as a 502.

    Same convention as ``ai_platform.trader_override.trader_override._run``.
    """
    try:
        return await fetch_rows(sql, params)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _to_timestamp(value: str | None) -> datetime | None:
    """Undo ``ai_platform.backend.db.json_safe``'s date/datetime -> ISO
    string conversion, ahead of a query (:func:`order_override_queries.
    insert_audit_sql`) that ``::timestamp``-casts its date parameters.

    Same fix, same reasoning, as
    ``ai_platform.trader_override.trader_override._to_timestamp`` -- every
    row read via ``_run``/``fetch_rows`` already has its date columns
    stringified, but asyncpg requires a real ``datetime``/``date`` object
    for a ``::timestamp``-cast parameter, not a string.
    """
    return datetime.fromisoformat(value) if value else None


class OrderOverrideRequest(BaseModel):
    """Body for submitting one order update.

    ``customer_name`` is accepted and logged for a paper trail, but never
    written to ``order_test`` -- see the module docstring. Pydantic rejects
    a non-numeric ``cargo_weight_min``/``cargo_weight_max`` with a 422
    before this ever reaches the database.
    """

    order_id: str = Field(min_length=1, max_length=64)
    order_seq: int = Field(ge=1)
    entered_by: str = Field(min_length=1, max_length=120)
    # Why the trader is making this change; logged to the audit trail.
    reason: str = Field(min_length=1, max_length=500)
    laycan_start: str | None = None
    laycan_end: str | None = None
    cargo_weight_min: float | None = Field(default=None, ge=0)
    cargo_weight_max: float | None = Field(default=None, ge=0)
    customer_name: str | None = Field(default=None, max_length=200)


@router.get("/orders")
async def list_orders() -> list[dict[str, Any]]:
    """Order picker: every order and its laycan window.

    Returns
    -------
    list of dict
        One row per ``(order_id, order_seq)``, backing the override form's
        order dropdown.
    """
    sql, params = ooq.orders_sql()
    return await _run(sql, params)


@router.get("/orders/{order_id}")
async def order_records(order_id: str) -> list[dict[str, Any]]:
    """Every ``order_seq`` record sharing ``order_id`` (empty list if none)."""
    return await _run(*ooq.order_records_sql(order_id))


@router.get("/orders/{order_id}/{order_seq}")
async def order_detail(order_id: str, order_seq: int) -> dict[str, Any]:
    """The current on-file record for one order, for the "current record" panel.

    Parameters
    ----------
    order_id : str
        Order identifier.
    order_seq : int
        Which of the orders sharing ``order_id`` this is.

    Returns
    -------
    dict
        The order's ``order_test`` row.

    Raises
    ------
    HTTPException
        404 if the order doesn't exist.
    """
    rows = await _run(*ooq.order_detail_sql(order_id, order_seq))
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"Unknown order: order_id={order_id!r}, order_seq={order_seq}"
        )
    return rows[0]


@router.post("", status_code=201)
async def submit_order_override(body: OrderOverrideRequest) -> dict[str, Any]:
    """Record one trader-entered laycan update by updating the order in place.

    Parameters
    ----------
    body : OrderOverrideRequest
        Order, who's entering it, and the laycan window/cargo weight range
        they have fresh word on.

    Returns
    -------
    dict
        The ``order_test`` row after the update.

    Raises
    ------
    HTTPException
        400 if ``cargo_weight_max`` is less than ``cargo_weight_min`` (both
        given); 404 if ``(order_id, order_seq)`` isn't a real order in
        ``order_test``;
        502 on a database failure.
    """
    if (
        body.cargo_weight_min is not None
        and body.cargo_weight_max is not None
        and body.cargo_weight_max < body.cargo_weight_min
    ):
        raise HTTPException(
            status_code=400, detail="cargo_weight_max cannot be less than cargo_weight_min."
        )

    # Doubles as the existence check (empty result -> 404) and as the
    # "before" snapshot the audit log needs -- same combined read
    # trader_override_queries.tonnage_row_snapshot_sql does for tonnage.
    snapshot_rows = await _run(*ooq.order_detail_sql(body.order_id, body.order_seq))
    if not snapshot_rows:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown order: order_id={body.order_id!r}, order_seq={body.order_seq}",
        )
    snapshot = snapshot_rows[0]

    rows = await _run(
        *ooq.update_order_sql(
            body.order_id,
            body.order_seq,
            laycan_start=body.laycan_start,
            laycan_end=body.laycan_end,
            cargo_weight_min=body.cargo_weight_min,
            cargo_weight_max=body.cargo_weight_max,
        )
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Order no longer exists for order_id={body.order_id!r}, "
                f"order_seq={body.order_seq} "
                "-- it may have just been deleted elsewhere. Refresh and try again."
            ),
        )
    new_row = rows[0]

    try:
        await fetch_rows(
            *ooq.insert_audit_sql(
                body.order_id,
                body.order_seq,
                body.entered_by,
                reason=body.reason,
                # The audit log's "new" side is the update's own effective
                # values, not the raw request body -- a field the trader
                # left blank resolved to the order's old value via
                # COALESCE, so logging the (blank) body field here would
                # make an untouched field look cleared. See insert_audit_sql.
                laycan_start=_to_timestamp(new_row["laycan_start"]),
                laycan_end=_to_timestamp(new_row["laycan_end"]),
                cargo_weight_min=new_row["cargo_weight_min"],
                cargo_weight_max=new_row["cargo_weight_max"],
                customer_name=body.customer_name,
                old_laycan_start=_to_timestamp(snapshot["laycan_start"]),
                old_laycan_end=_to_timestamp(snapshot["laycan_end"]),
                old_cargo_weight_min=snapshot["cargo_weight_min"],
                old_cargo_weight_max=snapshot["cargo_weight_max"],
            )
        )
    except asyncpg.UndefinedTableError:
        # order_override_audit is a log, not the source of truth -- the
        # order_test update above already committed, so a missing audit
        # table (its setup SQL never applied) must not fail the submission.
        logger.warning(
            "order_override_audit table is missing; run order_override_audit_setup.sql. "
            "order_test update already committed, continuing"
        )
    except Exception:
        # Any other failure is a real bug (bad cast, wrong column, ...): keep
        # the committed update, but log the full traceback so it isn't hidden.
        logger.exception(
            "order_override_audit insert failed for order_id=%r, order_seq=%d; "
            "order_test update already committed, continuing",
            body.order_id, body.order_seq,
        )

    return new_row


@router.delete("")
async def delete_order(
    order_id: str,
    order_seq: int,
    entered_by: str = Query(min_length=1, max_length=120),
    reason: str = Query(min_length=1, max_length=500),
) -> dict[str, Any]:
    """Delete one order from ``order_test`` and log the deletion to the audit trail.

    Only the named ``(order_id, order_seq)`` is removed. The audit entry
    (``action`` = ``DELETE``, removed values on the ``old_*`` side) is
    written after the delete commits and, like :func:`submit_order_override`,
    never fails it.

    Raises
    ------
    HTTPException
        404 if the order doesn't exist; 502 on a database failure.
    """
    rows = await _run(*ooq.delete_order_sql(order_id, order_seq))
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown order: order_id={order_id!r}, order_seq={order_seq}",
        )
    old = rows[0]
    try:
        await fetch_rows(
            *ooq.insert_audit_sql(
                order_id,
                order_seq,
                entered_by,
                reason=reason,
                old_laycan_start=_to_timestamp(old["laycan_start"]),
                old_laycan_end=_to_timestamp(old["laycan_end"]),
                old_cargo_weight_min=old["cargo_weight_min"],
                old_cargo_weight_max=old["cargo_weight_max"],
                action="DELETE",
            )
        )
    except Exception:
        logger.exception(
            "order_override_audit insert failed for deleted order_id=%r, order_seq=%d; "
            "order_test delete already committed, continuing",
            order_id, order_seq,
        )
    return {"deleted": True, "order_id": order_id, "order_seq": order_seq}


@router.get("/audit")
async def audit(order_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Order override audit trail, newest first.

    Scoped strictly to submissions made through this form -- reads
    ``order_override_audit`` only, never ``order_test`` itself.

    Parameters
    ----------
    order_id : str or None
        Restrict to one order's submissions, or omit for every order.
    limit : int
        Maximum rows to return, defaults to 200.

    Returns
    -------
    list of dict
        One row per submission ever made, matching the criterion. Empty
        if ``order_override_audit`` doesn't exist yet -- see
        :func:`submit_order_override`, which never fails on the audit
        table's own absence either.
    """
    sql, params = ooq.audit_sql(order_id, limit)
    try:
        return await fetch_rows(sql, params)
    except asyncpg.UndefinedTableError:
        logger.warning("order_override_audit table is missing; reporting empty audit trail")
        return []
    except Exception as exc:
        # A real failure must show up, not masquerade as "no updates yet".
        logger.exception("order_override_audit read failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
