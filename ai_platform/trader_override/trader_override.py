"""Trader status edits with mandatory, transactional before/after audit logging."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_platform.app.auth import current_trader
from ai_platform.backend.db import fetch_rows
from ai_platform.backend.logging_utils import get_logger
from ai_platform.trader_override import trader_override_queries as toq

router = APIRouter(
    prefix="/api/trader-override",
    tags=["trader-override"],
    dependencies=[Depends(current_trader)],
)
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

    Identity comes from the signed session; the body contains only edits.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    vessel_id: str = Field(min_length=1, max_length=64)
    # tonnage_row_key of the existing tonnage_test row this submission
    # replaces -- from a row GET /vessels/{vessel_id} returned; the vessel's
    # newest row by default, or an older one if the trader clicked a
    # different row in Current Record. See override_tonnage_row_sql.
    base_tonnage_row_key: str = Field(min_length=1, max_length=128)
    override_status: toq.OverrideStatus
    open_area: str | None = Field(default=None, max_length=120)
    open_date_start: str | None = None
    open_date_end: str | None = None
    order_assignment: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_dates(self):
        from datetime import date

        start = (
            date.fromisoformat(self.open_date_start) if self.open_date_start else None
        )
        end = date.fromisoformat(self.open_date_end) if self.open_date_end else None
        if start and end and start > end:
            raise ValueError("Open window start must not follow its end")
        if self.order_assignment and (
            not self.order_assignment.isdigit()
            or int(self.order_assignment) > 9223372036854775807
        ):
            raise ValueError("Order assignment must be a valid positive bigint")
        return self


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
async def submit_override(
    body: OverrideRequest, trader: str = Depends(current_trader)
) -> dict[str, Any]:
    """Lock the baseline and commit the edit and audit record together."""
    from ai_platform.backend.clock import overridden_working_date
    from ai_platform.backend.db import TIMEOUT_SECONDS, _get_pool, json_safe

    try:
        pool = await _get_pool()
        async with pool.acquire(timeout=TIMEOUT_SECONDS) as connection:  # noqa: SIM117
            async with connection.transaction():
                pinned = overridden_working_date()
                if pinned:
                    await connection.execute(
                        "SELECT set_config('oi.working_date', $1, true)",
                        pinned.isoformat(),
                    )
                sql, params = toq.tonnage_row_snapshot_sql(
                    body.base_tonnage_row_key, body.vessel_id
                )
                snapshot = await connection.fetchrow(sql + " FOR UPDATE", *params)
                if not snapshot:
                    raise HTTPException(
                        409,
                        "Baseline changed or is unavailable. Refresh and try again.",
                    )
                from datetime import date

                start = (
                    date.fromisoformat(body.open_date_start)
                    if body.open_date_start
                    else snapshot["open_date_start"]
                )
                end = (
                    date.fromisoformat(body.open_date_end)
                    if body.open_date_end
                    else snapshot["open_date_end"]
                )
                if start and end and str(start)[:10] > str(end)[:10]:
                    raise HTTPException(
                        422, "Effective open window start follows its end"
                    )
                sql, params = toq.override_tonnage_row_sql(
                    body.base_tonnage_row_key,
                    body.vessel_id,
                    body.override_status,
                    open_area=body.open_area,
                    open_date_start=body.open_date_start,
                    open_date_end=body.open_date_end,
                    order_assignment=body.order_assignment,
                )
                new_row = await connection.fetchrow(sql, *params)
                if not new_row:
                    raise HTTPException(409, "Baseline changed. Refresh and try again.")
                sql, params = toq.insert_audit_sql(
                    body.vessel_id,
                    new_row["commercial_status"],
                    trader,
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
                await connection.fetchrow(sql, *params)
        return {key: json_safe(value) for key, value in new_row.items()}
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Override transaction failed")
        raise HTTPException(
            502, "Override could not be saved with its audit record."
        ) from error


@router.get("/audit")
async def audit(
    vessel_id: str | None = None, limit: int = Query(default=200, ge=1, le=1000)
) -> list[dict[str, Any]]:
    """Read the audit trail; storage errors are surfaced to the caller."""
    return await _run(*toq.audit_sql(vessel_id, limit))
