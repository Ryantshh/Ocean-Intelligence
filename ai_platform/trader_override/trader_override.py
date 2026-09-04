"""Trader Status Override endpoints (Epic 3 / US-3.1).

Lets a trader manually record a vessel's commercial status heard privately
(via broker/messaging) before it reaches Shipfix/the pipeline. The
override goes straight into ``public.tonnage_test`` -- not a separate
overrides table -- by replacing the trader's chosen baseline row with a
copy carrying their edits (see
:func:`trader_override_queries.override_tonnage_row_sql`), so the
Dashboard tab's own ``vessel_current_status`` view (which always takes the
newest ``tonnage_test`` row per vessel) picks the change up automatically,
with no change to any dashboard-side code.

A successful submission is also logged to ``public.trader_override_audit``
(see ``ai_platform/trader_override/trader_override_audit_setup.sql``) --
purely so the Audit Trail table can show submissions scoped to this form
specifically, since a row in ``tonnage_test`` itself carries no marker of
having come from a trader rather than Shipfix. That table is optional: if
it hasn't been created, both the audit insert (in :func:`submit_override`)
and the audit read (:func:`audit`) fail quietly -- a submission still
succeeds and lands in ``tonnage_test``, the Audit Trail table just reports
empty instead of erroring.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai_platform.backend.db import fetch_rows
from ai_platform.backend.logging_utils import get_logger
from ai_platform.trader_override import trader_override_queries as toq

router = APIRouter(prefix="/api/trader-override", tags=["trader-override"])
logger = get_logger("trader_override")


async def _run(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """Execute one query and surface failures as a 502.

    Same convention as ``ai_platform.app.api.dashboard._run``.

    Parameters
    ----------
    sql : str
        Parameterised statement from ``trader_override_queries``.
    params : list
        Positional parameters.

    Returns
    -------
    list of dict
        Result rows.

    Raises
    ------
    HTTPException
        502 when the database cannot be reached or queried.
    """
    try:
        return await fetch_rows(sql, params)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class OverrideRequest(BaseModel):
    """Body for submitting one trader status update.

    Pydantic rejects an unrecognised ``override_status`` or a blank
    ``entered_by`` with a 422 before this ever reaches the database.
    """

    vessel_id: str = Field(min_length=1, max_length=64)
    # tonnage_row_key of the existing tonnage_test row this submission
    # replaces -- from a row GET /vessels/{vessel_id} returned; the vessel's
    # newest row by default, or an older one if the trader clicked a
    # different row in Current Record. See override_tonnage_row_sql.
    base_tonnage_row_key: str = Field(min_length=1, max_length=128)
    override_status: toq.OverrideStatus
    entered_by: str = Field(min_length=1, max_length=120)
    open_area: str | None = Field(default=None, max_length=120)
    open_date_start: str | None = None
    open_date_end: str | None = None
    order_assignment: str | None = Field(default=None, max_length=64)


@router.get("/vessels")
async def list_vessels() -> list[dict[str, Any]]:
    """Vessel picker: every vessel and its latest raw reported status.

    Returns
    -------
    list of dict
        One row per vessel (``vessel_id``, ``commercial_status``,
        ``update_date``), backing the override form's vessel dropdown.
    """
    sql, params = toq.vessels_sql()
    return await _run(sql, params)


@router.get("/vessels/{vessel_id}")
async def vessel_detail(vessel_id: str) -> list[dict[str, Any]]:
    """Every on-file record for one vessel, newest first, for the "current
    record" table shown once a trader picks a vessel.

    ``vessel_id`` is not unique in ``tonnage_test`` -- see
    ``trader_override_queries.vessel_history_sql`` -- so this returns every
    reported row for the vessel, not just the latest, letting the trader see
    its reporting history before deciding what to override. The first row is
    the current one.

    Parameters
    ----------
    vessel_id : str
        Vessel identifier.

    Returns
    -------
    list of dict
        The vessel's ``tonnage_test`` rows, newest first.

    Raises
    ------
    HTTPException
        404 if the vessel doesn't exist.
    """
    rows = await _run(*toq.vessel_history_sql(vessel_id))
    if not rows:
        raise HTTPException(status_code=404, detail=f"Unknown vessel_id: {vessel_id!r}")
    return rows


@router.post("", status_code=201)
async def submit_override(body: OverrideRequest) -> dict[str, Any]:
    """Record one trader-entered status update by replacing its baseline row.

    Also logs the submission to ``trader_override_audit`` once the
    ``tonnage_test`` swap succeeds -- the audit entry is a record of what
    happened, not a second attempt at the real write, so a failure logging
    it does not roll back or fail the swap, which has already committed by
    that point.

    Parameters
    ----------
    body : OverrideRequest
        Vessel, the baseline row being replaced, status, who's entering it,
        and whatever else the trader has fresh word on.

    Returns
    -------
    dict
        The ``tonnage_test`` row that was inserted in the baseline row's
        place.

    Raises
    ------
    HTTPException
        404 if ``vessel_id`` isn't a real vessel in ``tonnage_test``, or if
        ``base_tonnage_row_key`` no longer matches an existing row for it
        (edited/deleted concurrently, or a stale key from an old page load);
        502 on a database failure (including an ``order_assignment`` that
        isn't a valid integer -- rejected by the ``::bigint`` cast in the
        insert).
    """
    exists = await _run(*toq.vessel_exists_sql(body.vessel_id))
    if not exists:
        raise HTTPException(
            status_code=404, detail=f"Unknown vessel_id: {body.vessel_id!r}"
        )

    # Read the baseline row's own values before it's swapped out, purely so
    # the audit log below can record what each field changed *from* -- also
    # doubles as the existence check for base_tonnage_row_key, ahead of the
    # swap itself.
    snapshot_rows = await _run(*toq.tonnage_row_snapshot_sql(body.base_tonnage_row_key, body.vessel_id))
    if not snapshot_rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Record to override no longer exists for vessel_id={body.vessel_id!r} "
                "-- it may have just been edited or reloaded elsewhere. Refresh and try again."
            ),
        )
    snapshot = snapshot_rows[0]

    rows = await _run(
        *toq.override_tonnage_row_sql(
            body.base_tonnage_row_key,
            body.vessel_id,
            body.override_status,
            open_area=body.open_area,
            open_date_start=body.open_date_start,
            open_date_end=body.open_date_end,
            order_assignment=body.order_assignment,
        )
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Record to override no longer exists for vessel_id={body.vessel_id!r} "
                "-- it may have just been edited or reloaded elsewhere. Refresh and try again."
            ),
        )
    new_row = rows[0]

    try:
        await _run(
            *toq.insert_audit_sql(
                body.vessel_id,
                # The audit log's "new" side is the swap's own effective
                # values, not the raw request body -- a field the trader
                # left blank resolved to the base row's old value via
                # COALESCE, so logging the (blank) body field here would
                # make an untouched field look cleared. See insert_audit_sql.
                new_row["commercial_status"],
                body.entered_by,
                open_area=new_row["open_area"],
                open_date_start=new_row["open_date_start"],
                open_date_end=new_row["open_date_end"],
                order_assignment=new_row["order_id"],
                old_override_status=snapshot["commercial_status"],
                old_open_area=snapshot["open_area"],
                old_open_date_start=snapshot["open_date_start"],
                old_open_date_end=snapshot["open_date_end"],
                old_order_assignment=snapshot["order_id"],
            )
        )
    except HTTPException:
        # trader_override_audit is a log, not the source of truth -- the
        # tonnage_test insert above already committed, so a missing/broken
        # audit table (e.g. its setup SQL was never applied) must not fail
        # the submission itself, only the Audit Trail table's own read.
        logger.warning(
            "trader_override_audit insert failed for vessel_id=%r; "
            "tonnage_test insert already committed, continuing",
            body.vessel_id,
        )

    return rows[0]


@router.get("/audit")
async def audit(vessel_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Trader override audit trail, newest first.

    Scoped strictly to submissions made through this form -- reads
    ``trader_override_audit`` only, never ``tonnage_test`` itself.

    Parameters
    ----------
    vessel_id : str or None
        Restrict to one vessel's submissions, or omit for every vessel.
    limit : int
        Maximum rows to return, defaults to 200.

    Returns
    -------
    list of dict
        One row per submission ever made, matching the criterion. Empty
        if ``trader_override_audit`` doesn't exist yet -- see
        :func:`submit_override`, which never fails on the audit table's
        own absence either.
    """
    sql, params = toq.audit_sql(vessel_id, limit)
    try:
        return await _run(sql, params)
    except HTTPException:
        logger.warning("trader_override_audit read failed; reporting empty audit trail")
        return []
