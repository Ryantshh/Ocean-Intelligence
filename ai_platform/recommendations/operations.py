"""Database-backed admission and operational telemetry across app instances."""

import asyncio
import logging
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException

from ai_platform.backend.db import _get_pool

logger = logging.getLogger(__name__)
MAX_CONCURRENT = 4
PER_TRADER_PER_MINUTE = 6
REQUEST_TIMEOUT_SECONDS = 90


async def admit(trader):
    identifier = uuid4()
    pool = await _get_pool()
    async with pool.acquire(timeout=10) as connection, connection.transaction():
        await connection.execute("SET LOCAL statement_timeout='5s'")
        await connection.execute("SELECT pg_advisory_xact_lock(483,1)")
        counts = await connection.fetchrow(
            """SELECT
          count(*) FILTER (WHERE actor=$1 AND started_at>now()-interval '1 minute') AS recent,
          count(*) FILTER (WHERE finished_at IS NULL AND lease_until>now()) AS active
          FROM public.oi_workflow_requests
          WHERE started_at>now()-interval '3 minutes'""",
            trader,
        )
        if (
            counts["recent"] >= PER_TRADER_PER_MINUTE
            or counts["active"] >= MAX_CONCURRENT
        ):
            raise HTTPException(
                429,
                "The review queue is busy. Try again in a minute.",
                headers={"Retry-After": "60"},
            )
        await connection.execute(
            "INSERT INTO public.oi_workflow_requests(id,actor,lease_until) VALUES ($1,$2,now()+interval '2 minutes')",
            identifier,
            trader,
        )
    return identifier


async def finish(identifier, outcome, elapsed, details):
    pool = await _get_pool()
    async with pool.acquire(timeout=5) as connection:
        await connection.execute(
            """UPDATE public.oi_workflow_requests SET finished_at=now(),
           outcome=$2,latency_ms=$3,prompt_tokens=$4,completion_tokens=$5,recommendation_id=$6
           WHERE id=$1""",
            identifier,
            outcome,
            elapsed,
            details.get("prompt_tokens"),
            details.get("completion_tokens"),
            details.get("recommendation_id"),
            timeout=5,
        )


@asynccontextmanager
async def workflow_slot(trader):
    identifier = await admit(trader)
    started = perf_counter()
    details = {}
    outcome = "failed"
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            yield details
        outcome = details.get("outcome", "completed")
    except TimeoutError as error:
        outcome = "timeout"
        raise HTTPException(
            504, "The review timed out. Try again or select rules only."
        ) from error
    finally:
        elapsed = round((perf_counter() - started) * 1000)
        try:
            await asyncio.shield(finish(identifier, outcome, elapsed, details))
        except Exception as error:  # noqa: BLE001 - telemetry must not hide a saved result
            logger.warning("Review telemetry finish failed: %s", type(error).__name__)
        logger.info(
            "recommendation_request id=%s outcome=%s latency_ms=%s",
            identifier,
            outcome,
            elapsed,
        )


async def metrics():
    pool = await _get_pool()
    async with pool.acquire(timeout=10) as connection:
        row = await connection.fetchrow(
            """SELECT count(*) AS requests,
          count(*) FILTER (WHERE outcome='completed') AS completed,
          count(*) FILTER (WHERE outcome='degraded') AS degraded,
          count(*) FILTER (WHERE outcome IN ('failed','timeout') OR (finished_at IS NULL AND lease_until<=now())) AS failed,
          count(*) FILTER (WHERE finished_at IS NULL AND lease_until>now()) AS active,
          COALESCE(sum(prompt_tokens),0)::bigint AS prompt_tokens,
          COALESCE(sum(completion_tokens),0)::bigint AS completion_tokens,
          count(*) FILTER (WHERE prompt_tokens IS NULL) AS usage_unknown,
          percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
          FROM public.oi_workflow_requests WHERE started_at>now()-interval '24 hours'""",
            timeout=10,
        )
        labels = await connection.fetchval(
            "SELECT count(DISTINCT recommendation_id) FROM public.oi_evaluation_labels",
            timeout=10,
        )
    return {
        **dict(row),
        "labelled_reviews": labels,
        "window_hours": 24,
        "limits": {
            "concurrent": MAX_CONCURRENT,
            "per_trader_per_minute": PER_TRADER_PER_MINUTE,
        },
    }
