"""Trader Status Override endpoints (Epic 3 / US-3.1).

Lets a trader manually record a vessel's commercial status heard privately
(via broker/messaging) before it reaches Shipfix/the pipeline. Reads/writes
``public.vessel_status_overrides`` -- see
``infra/sql/trader_override_setup.sql``, which must be applied to the
database once before these endpoints will resolve.

Deliberately does not compute or serve any derived "effective status" for a
vessel from these overrides -- see that SQL file's header for why this is
out of scope here.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ai_platform.backend import trader_override_queries as toq
from ai_platform.backend.db import fetch_rows

router = APIRouter(prefix="/api/trader-override", tags=["trader-override"])


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
        502 when the database cannot be reached or queried -- most commonly
        because ``infra/sql/trader_override_setup.sql`` has not been applied
        to this database yet.
    """
    try:
        return await fetch_rows(sql, params)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class OverrideRequest(BaseModel):
    """Body for submitting one trader status override.

    Pydantic rejects an unrecognised ``override_status`` or a blank
    ``entered_by`` with a 422 before this ever reaches the database --
    the acceptance criterion that input is "validated ... before being
    saved."
    """

    vessel_id: str = Field(min_length=1, max_length=64)
    override_status: toq.OverrideStatus
    entered_by: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    open_area: str | None = Field(default=None, max_length=120)
    destination: str | None = Field(default=None, max_length=120)
    eta: str | None = None
    open_date_start: str | None = None
    open_date_end: str | None = None
    ballast_laden: Literal["LADEN", "BALLAST"] | None = None
    parent_zone: str | None = Field(default=None, max_length=120)


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
    """Record one trader-entered status override.

    Parameters
    ----------
    body : OverrideRequest
        Vessel, status, trader identity, and an optional note.

    Returns
    -------
    dict
        The inserted row.

    Raises
    ------
    HTTPException
        404 if ``vessel_id`` isn't a real vessel in ``tonnage_test``; 502 on
        a database failure.
    """
    exists = await _run(*toq.vessel_exists_sql(body.vessel_id))
    if not exists:
        raise HTTPException(
            status_code=404, detail=f"Unknown vessel_id: {body.vessel_id!r}"
        )

    rows = await _run(
        *toq.insert_override_sql(
            body.vessel_id,
            body.override_status,
            body.entered_by,
            body.note,
            open_area=body.open_area,
            destination=body.destination,
            eta=body.eta,
            open_date_start=body.open_date_start,
            open_date_end=body.open_date_end,
            ballast_laden=body.ballast_laden,
            parent_zone=body.parent_zone,
        )
    )
    return rows[0]


@router.get("/audit")
async def audit(vessel_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Full override history, newest first.

    Parameters
    ----------
    vessel_id : str or None
        Restrict to one vessel's history, or omit for every vessel.
    limit : int
        Maximum rows to return, defaults to 200.

    Returns
    -------
    list of dict
        One row per override ever submitted, matching the criterion.
    """
    sql, params = toq.audit_sql(vessel_id, limit)
    return await _run(sql, params)
