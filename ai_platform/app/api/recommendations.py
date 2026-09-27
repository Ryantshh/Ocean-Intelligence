"""Authenticated recommendation desk and shared decision ledger."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_platform.app.auth import current_trader
from ai_platform.backend.clock import working_date
from ai_platform.backend.db import fetch_rows
from ai_platform.recommendations import labels, operations
from ai_platform.recommendations import pg_store as store
from ai_platform.recommendations.orchestration import run_workflow
from ai_platform.recommendations.source import ORDER_SQL, VESSELS_SQL

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])
Trader = Annotated[str, Depends(current_trader)]


class Request(BaseModel):
    mode: Literal["ai", "rules"] = "ai"
    order_id: str = Field(min_length=1, max_length=64, pattern=r"^\d+$")


class Decision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    action: Literal["accept", "reject", "override"]
    vessel_id: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=0)
    request_id: UUID

    @model_validator(mode="after")
    def validate_selection(self):
        if self.action == "reject" and self.vessel_id is not None:
            raise ValueError("Reject must not select a vessel")
        if self.action != "reject" and not self.vessel_id:
            raise ValueError("Select a vessel for this decision")
        return self


@router.get("/session")
async def session(trader: Trader):
    return {"identifier": trader, "working_date": working_date().isoformat()}


@router.get("/orders")
async def orders(trader: Trader, q: str = Query(default="", max_length=80)):
    return await fetch_rows(
        """SELECT order_id::text AS order_id, load_port, load_zone,
        cargo_weight_min, laycan_start, laycan_end FROM public.order_test
        WHERE update_date::date <= $1 AND laycan_end::date >= $1
        AND (order_id::text ILIKE $2 OR load_port ILIKE $2)
        ORDER BY laycan_start, order_id LIMIT 50""",
        [working_date(), "%" + q + "%"],
    )


@router.get("")
async def recent(trader: Trader):
    return await store.recent()


@router.post("", status_code=201)
async def create(body: Request, trader: Trader):
    async with operations.workflow_slot(trader) as telemetry:
        as_of = working_date()
        rows = await fetch_rows(ORDER_SQL, [body.order_id, as_of])
        if not rows:
            raise HTTPException(404, "Order unavailable at the working date")
        vessels = await fetch_rows(VESSELS_SQL, [as_of])
        payload = await run_workflow(rows[0], vessels, as_of, mode=body.mode)
        saved = await store.save(payload, trader)
        reviews = payload.get("reviews", [])
        known_usage = all(review.get("usage") is not None for review in reviews)
        telemetry.update(
            recommendation_id=UUID(saved["id"]),
            outcome="degraded"
            if payload["review_status"] == "degraded"
            else "completed",
            prompt_tokens=sum(
                review["usage"].get("prompt_tokens", 0) for review in reviews
            )
            if known_usage
            else None,
            completion_tokens=sum(
                review["usage"].get("completion_tokens", 0) for review in reviews
            )
            if known_usage
            else None,
        )
        return saved


class EvaluationLabels(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    relevant: list[str] = Field(max_length=2000)
    forbidden: list[str] = Field(max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    complete_review: Literal[True]


@router.get("/metrics")
async def metrics(trader: Trader):
    return await operations.metrics()


@router.get("/evaluation/cases")
async def evaluation_cases(trader: Trader):
    return await labels.export_cases()


@router.post("/{identifier}/labels", status_code=201)
async def add_labels(identifier: UUID, body: EvaluationLabels, trader: Trader):
    return await labels.save(identifier, body, trader)


@router.get("/{identifier}")
async def read(identifier: UUID, trader: Trader):
    return await store.get(identifier)


@router.post("/{identifier}/decisions", status_code=201)
async def decision(identifier: UUID, body: Decision, trader: Trader):
    return await store.decide(identifier, body, trader)
