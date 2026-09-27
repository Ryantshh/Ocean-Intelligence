"""Shared, immutable PostgreSQL evidence and decision ledger."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from ai_platform.backend.clock import working_date
from ai_platform.backend.db import _get_pool, json_safe
from ai_platform.recommendations.source import ORDER_SQL, VESSEL_SQL

MAX_AGE_SECONDS = 1800


def plain(record):
    return {key: json_safe(value) for key, value in record.items()} if record else None


def payload_of(record):
    raw = record["payload"]
    return json.loads(raw) if isinstance(raw, str) else raw


def check_fresh(
    payload, created_at, order, vessel, selected_id, *, now=None, as_of=None
):
    now = now or datetime.now(UTC)
    as_of = as_of or working_date()
    if (now - created_at).total_seconds() > MAX_AGE_SECONDS or payload[
        "as_of"
    ] != as_of.isoformat():
        raise HTTPException(
            409, "Recommendation expired. Generate a new review before deciding."
        )
    items = (
        payload["candidates"]
        + payload["rejected"]
        + payload.get("held", [])
        + payload.get("not_shortlisted", [])
    )
    saved = next(
        (item["evidence"] for item in items if item["vessel_id"] == selected_id), None
    )
    if order != payload["order"] or vessel != saved or not vessel:
        raise HTTPException(
            409,
            "Order or vessel evidence changed. Generate a new review before deciding.",
        )


async def save(payload, trader):
    identifier = uuid4()
    pool = await _get_pool()
    async with pool.acquire() as connection:
        created = await connection.fetchval(
            "INSERT INTO public.oi_recommendations(id,payload,created_by) VALUES ($1,$2::jsonb,$3) RETURNING created_at",
            identifier,
            json.dumps(payload),
            trader,
        )
    return {
        "id": str(identifier),
        **payload,
        "created_by": trader,
        "created_at": created.isoformat(),
        "version": 0,
        "decisions": [],
    }


async def get(identifier):
    pool = await _get_pool()
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT * FROM public.oi_recommendations WHERE id=$1", identifier
        )
        if not row:
            raise HTTPException(404, "Recommendation not found")
        history = await connection.fetch(
            "SELECT * FROM public.oi_recommendation_decisions WHERE recommendation_id=$1 ORDER BY version DESC",
            identifier,
        )
    decisions = [
        {
            **plain(item),
            "request_id": str(item["request_id"]),
            "recommendation_id": str(identifier),
        }
        for item in history
    ]
    return {
        "id": str(identifier),
        **payload_of(row),
        "created_by": row["created_by"],
        "created_at": row["created_at"].isoformat(),
        "version": decisions[0]["version"] if decisions else 0,
        "decisions": decisions,
    }


async def recent():
    pool = await _get_pool()
    async with pool.acquire() as connection:
        rows = await connection.fetch("""SELECT r.id::text, r.created_at, r.created_by,
          r.payload->'order'->>'order_id' AS order_id, r.payload->>'review_status' AS review_status,
          (SELECT action FROM public.oi_recommendation_decisions d WHERE d.recommendation_id=r.id ORDER BY version DESC LIMIT 1) AS decision
          FROM public.oi_recommendations r ORDER BY created_at DESC LIMIT 20""")
    return [plain(row) for row in rows]


async def decide(identifier, body, trader):
    pool = await _get_pool()
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SET LOCAL lock_timeout = '5s'")
        row = await connection.fetchrow(
            "SELECT * FROM public.oi_recommendations WHERE id=$1 FOR UPDATE", identifier
        )
        if not row:
            raise HTTPException(404, "Recommendation not found")
        prior = await connection.fetchrow(
            "SELECT * FROM public.oi_recommendation_decisions WHERE request_id=$1",
            body.request_id,
        )
        if prior:
            if any(
                prior[key] != value
                for key, value in {
                    "recommendation_id": identifier,
                    "entered_by": trader,
                    "action": body.action,
                    "vessel_id": body.vessel_id,
                    "reason": body.reason,
                }.items()
            ):
                raise HTTPException(
                    409, "Request identifier already used for a different decision"
                )
        else:
            version = await connection.fetchval(
                "SELECT COALESCE(MAX(version),0) FROM public.oi_recommendation_decisions WHERE recommendation_id=$1",
                identifier,
            )
            if version != body.expected_version:
                raise HTTPException(
                    409,
                    "Another trader has recorded a decision. Reload this review first.",
                )
            payload = payload_of(row)
            eligible = {item["vessel_id"] for item in payload["candidates"]}
            known = eligible | {
                item["vessel_id"]
                for item in payload["rejected"]
                + payload.get("held", [])
                + payload.get("not_shortlisted", [])
            }
            if body.action == "accept" and body.vessel_id not in eligible:
                raise HTTPException(422, "Accept requires a shortlisted vessel")
            if body.action == "override" and body.vessel_id not in known:
                raise HTTPException(422, "Override requires an evaluated vessel")
            if body.action != "reject":
                # Brief SHARE locks prevent source writers changing evidence between
                # validation and the decision insert, including new report rows.
                await connection.execute(
                    "LOCK TABLE public.order_test, public.tonnage_test IN SHARE MODE"
                )
                as_of = working_date()
                order = plain(
                    await connection.fetchrow(
                        ORDER_SQL, payload["order"]["order_id"], as_of
                    )
                )
                vessel = plain(
                    await connection.fetchrow(VESSEL_SQL, body.vessel_id, as_of)
                )
                check_fresh(
                    payload,
                    row["created_at"],
                    order,
                    vessel,
                    body.vessel_id,
                    as_of=as_of,
                )
            await connection.execute(
                """INSERT INTO public.oi_recommendation_decisions
                (recommendation_id,version,request_id,action,vessel_id,entered_by,reason)
                VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                identifier,
                version + 1,
                body.request_id,
                body.action,
                body.vessel_id,
                trader,
                body.reason,
            )
    return await get(identifier)
