"""Order Status Override endpoints -- the order-side counterpart to
:mod:`ai_platform.trader_override.trader_override`.

Lets a trader record fresh word on an order's laycan window and cargo
weight range before it reaches Shipfix/the pipeline. Writes straight into
``public.order_test`` by updating the matching row in place -- ``order_id``
is unique per row here (unlike ``tonnage_test``'s ``vessel_id``), so there
is no copy/delete/insert swap to do, and no separate overrides table.

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

from fastapi import APIRouter, HTTPException
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
    entered_by: str = Field(min_length=1, max_length=120)
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
        One row per order, backing the override form's order dropdown.
    """
    sql, params = ooq.orders_sql()
    return await _run(sql, params)


@router.get("/orders/{order_id}")
async def order_detail(order_id: str) -> dict[str, Any]:
    """The current on-file record for one order, for the "current record" panel.

    Parameters
    ----------
    order_id : str
        Order identifier.

    Returns
    -------
    dict
        The order's ``order_test`` row.

    Raises
    ------
    HTTPException
        404 if the order doesn't exist.
    """
    rows = await _run(*ooq.order_detail_sql(order_id))
    if not rows:
        raise HTTPException(status_code=404, detail=f"Unknown order_id: {order_id!r}")
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
        given); 404 if ``order_id`` isn't a real order in ``order_test``;
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
    snapshot_rows = await _run(*ooq.order_detail_sql(body.order_id))
    if not snapshot_rows:
        raise HTTPException(status_code=404, detail=f"Unknown order_id: {body.order_id!r}")
    snapshot = snapshot_rows[0]

    rows = await _run(
        *ooq.update_order_sql(
            body.order_id,
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
                f"Order no longer exists for order_id={body.order_id!r} "
                "-- it may have just been deleted elsewhere. Refresh and try again."
            ),
        )
    new_row = rows[0]

    try:
        await _run(
            *ooq.insert_audit_sql(
                body.order_id,
                body.entered_by,
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
    except HTTPException as exc:
        # order_override_audit is a log, not the source of truth -- the
        # order_test update above already committed, so a missing/broken
        # audit table (its setup SQL never applied) must not fail the
        # submission itself, only the Audit Trail table's own read.
        logger.warning(
            "order_override_audit insert failed for order_id=%r (%s); "
            "order_test update already committed, continuing",
            body.order_id, exc.detail,
        )

    return new_row


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
        return await _run(sql, params)
    except HTTPException:
        logger.warning("order_override_audit read failed; reporting empty audit trail")
        return []
