"""Append-only human labels, separate from trading decisions and model output."""

from uuid import uuid4

from fastapi import HTTPException

from ai_platform.backend.db import _get_pool
from ai_platform.recommendations.pg_store import payload_of


def fleet(payload):
    return (
        payload["candidates"]
        + payload["rejected"]
        + payload.get("held", [])
        + payload.get("not_shortlisted", [])
    )


def validate_labels(payload, relevant, forbidden):
    if len(relevant) != len(set(relevant)) or len(forbidden) != len(set(forbidden)):
        raise HTTPException(422, "Each vessel may appear only once in a label set.")
    if set(relevant) & set(forbidden):
        raise HTTPException(422, "A vessel cannot be both suitable and forbidden.")
    known = {item["vessel_id"] for item in fleet(payload)}
    if not (set(relevant) | set(forbidden)) <= known:
        raise HTTPException(422, "Labels must reference vessels in this review.")


async def save(identifier, body, trader):
    pool = await _get_pool()
    async with pool.acquire(timeout=10) as connection:
        row = await connection.fetchrow(
            "SELECT payload FROM public.oi_recommendations WHERE id=$1", identifier
        )
        if not row:
            raise HTTPException(404, "Recommendation not found")
        validate_labels(payload_of(row), body.relevant, body.forbidden)
        label_id = uuid4()
        await connection.execute(
            """INSERT INTO public.oi_evaluation_labels(id,recommendation_id,reviewer,relevant,forbidden,rationale)
          VALUES ($1,$2,$3,$4,$5,$6)""",
            label_id,
            identifier,
            trader,
            body.relevant,
            body.forbidden,
            body.rationale,
        )
    return {"id": str(label_id), "reviewer": trader}


async def export_cases():
    pool = await _get_pool()
    async with pool.acquire(timeout=10) as connection:
        rows = await connection.fetch(
            """SELECT DISTINCT ON (l.recommendation_id) l.*,r.payload
          FROM public.oi_evaluation_labels l JOIN public.oi_recommendations r ON r.id=l.recommendation_id
          ORDER BY l.recommendation_id,l.created_at DESC,l.id DESC""",
            timeout=30,
        )
    cases = []
    for row in rows:
        payload = payload_of(row)
        cases.append(
            {
                "id": str(row["recommendation_id"]),
                "label_source": "trader-labelled saved review",
                "label_id": str(row["id"]),
                "reviewer": row["reviewer"],
                "labelled_at": row["created_at"].isoformat(),
                "rationale": row["rationale"],
                "as_of": payload["as_of"],
                "order": payload["order"],
                "vessels": [item["evidence"] for item in fleet(payload)],
                "relevant": list(row["relevant"]),
                "forbidden": list(row["forbidden"]),
            }
        )
    return cases
