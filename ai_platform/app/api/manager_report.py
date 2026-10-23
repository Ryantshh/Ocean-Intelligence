"""Read-only commercial brief; historical flows and current stock stay separate."""
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from ai_platform.app.api.dashboard import _run
from ai_platform.backend.dashboard_queries import reference_times_sql

router = APIRouter(prefix="/api/manager-report", tags=["manager report"])


@router.get("")
async def manager_report(
    start: date | None = None, end: date | None = None,
    region: str = Query("", max_length=100),
    days: int = Query(7, ge=1, le=90),
):
    reference = (await _run(*reference_times_sql()))[0]
    now = datetime.fromisoformat(reference["orders_now"]).replace(tzinfo=None)
    end = end or now.date() - timedelta(days=1)
    start = start or end - timedelta(days=days - 1)
    if start > end or (end - start).days > 89 or end >= now.date():
        raise HTTPException(422, "Choose 1–90 completed days before the order simulation date.")
    since = datetime.combine(start, time.min)
    until = datetime.combine(end + timedelta(days=1), time.min)
    previous = since - (until - since)
    params = [previous, since, until, region.strip()]
    scope = "date_received >= $1 AND date_received < $3 AND ($4 = '' OR load_zone ILIKE '%' || $4 || '%')"
    metrics = (await _run(f"""
        SELECT count(*) FILTER (WHERE date_received >= $2) AS orders,
          count(*) FILTER (WHERE date_received < $2) AS previous_orders,
          count(*) FILTER (WHERE date_received >= $2 AND (cargo_weight_min IS NULL OR cargo_weight_max IS NULL)) AS missing_weight,
          count(*) FILTER (WHERE date_received >= $2 AND (laycan_start IS NULL OR laycan_end IS NULL)) AS missing_laycan,
          count(*) FILTER (WHERE date_received >= $2 AND (cargo_weight_min > cargo_weight_max OR laycan_start > laycan_end OR cargo_weight_min < 0 OR cargo_weight_max < 0)) AS invalid_ranges,
          count(*) FILTER (WHERE date_received >= $2 AND laycan_end::date >= orders_reference_now()::date AND laycan_end::date <= orders_reference_now()::date + 7) AS due_soon,
          count(*) FILTER (WHERE date_received >= $2 AND laycan_end::date < orders_reference_now()::date) AS elapsed_laycans,
          count(*) FILTER (WHERE date_received >= $2 AND (cargo_type IS NULL OR trim(cargo_type) = '')) AS missing_cargo,
          sum(cargo_weight_min) FILTER (WHERE date_received >= $2 AND cargo_weight_min >= 0 AND cargo_weight_max >= cargo_weight_min) AS weight_min,
          sum(cargo_weight_max) FILTER (WHERE date_received >= $2 AND cargo_weight_min >= 0 AND cargo_weight_max >= cargo_weight_min) AS weight_max,
          count(*) FILTER (WHERE date_received >= $2 AND cargo_weight_min >= 0 AND cargo_weight_max >= cargo_weight_min) AS complete_weight,

          count(DISTINCT order_id) FILTER (WHERE date_received >= $2 AND occurrences > 1) AS repeated_ids
        FROM (SELECT *, count(*) OVER (PARTITION BY order_id) AS occurrences
          FROM public.order_test WHERE date_received < orders_reference_now()) o WHERE {scope}
    """, params))[0]
    regions = await _run(f"""SELECT coalesce(nullif(load_zone, ''), 'Unspecified') AS region,
        count(*) FILTER (WHERE date_received >= $2) AS orders,
        count(*) FILTER (WHERE date_received < $2) AS previous_orders
        FROM public.order_test WHERE {scope} GROUP BY 1 ORDER BY orders DESC, region LIMIT 12""", params)
    records = await _run(f"""SELECT order_id::text AS order_id, order_seq, cargo_type, load_zone, laycan_start, laycan_end,
        cargo_weight_min, cargo_weight_max FROM public.order_test WHERE {scope} AND date_received >= $2
        ORDER BY laycan_start ASC NULLS LAST, order_id, order_seq LIMIT 20""", params)
    fleet = await _run("""SELECT dashboard_status AS status, count(*) AS count
        FROM vessel_current_status WHERE $1 = '' OR EXISTS
        (SELECT 1 FROM unnest(parent_zones) z WHERE z ILIKE '%' || $1 || '%')
        GROUP BY dashboard_status ORDER BY dashboard_status""", [region.strip()])
    daily = await _run(f"""SELECT date_received::date AS day, count(*) AS orders,
        CASE WHEN date_received >= $2 THEN 'current' ELSE 'previous' END AS period
        FROM public.order_test WHERE {scope} GROUP BY 1, 3 ORDER BY 1""", params)
    return {"report_id": str(uuid4()), "daily": daily, "start": start, "end": end, "previous_start": previous.date(),
            "previous_end": start - timedelta(days=1), "region": region.strip(),
            "generated_at": datetime.now(UTC), "reference": reference,
            "metrics": metrics, "regions": regions, "orders": records, "fleet": fleet}


Issue = Literal["all", "missing_weight", "missing_laycan", "missing_cargo", "invalid_ranges", "due_soon", "elapsed_laycans", "repeated_ids"]
ISSUE_FILTERS = {
    "all": "TRUE",
    "missing_weight": "(cargo_weight_min IS NULL OR cargo_weight_max IS NULL)",
    "missing_laycan": "(laycan_start IS NULL OR laycan_end IS NULL)",
    "missing_cargo": "(cargo_type IS NULL OR trim(cargo_type) = '')",
    "invalid_ranges": "(cargo_weight_min > cargo_weight_max OR laycan_start > laycan_end OR cargo_weight_min < 0 OR cargo_weight_max < 0)",
    "due_soon": "laycan_end::date BETWEEN orders_reference_now()::date AND orders_reference_now()::date + 7",
    "elapsed_laycans": "laycan_end::date < orders_reference_now()::date",
    "repeated_ids": "occurrences > 1",
}


@router.get("/evidence")
async def report_evidence(start: date, end: date, region: str = Query("", max_length=100),
                          issue: Issue = "all", offset: int = Query(0, ge=0)):
    reference = (await _run(*reference_times_sql()))[0]
    now = datetime.fromisoformat(reference["orders_now"]).date()
    if start > end or (end - start).days > 89 or end >= now:
        raise HTTPException(422, "Choose 1–90 completed days before the order simulation date.")
    rows = await _run(f"""SELECT order_id::text AS order_id, order_seq, cargo_type, load_zone,
        laycan_start, laycan_end, cargo_weight_min, cargo_weight_max, count(*) OVER() AS total_count
        FROM (SELECT *, count(*) OVER(PARTITION BY order_id) AS occurrences
              FROM public.order_test WHERE date_received < orders_reference_now()) o
        WHERE date_received >= $1 AND date_received < $2
          AND ($3 = '' OR load_zone ILIKE '%' || $3 || '%') AND {ISSUE_FILTERS[issue]}
        ORDER BY laycan_end ASC NULLS LAST, order_id, order_seq LIMIT 25 OFFSET $4""",
        [datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min), region.strip(), offset])
    return {"rows": rows, "offset": offset, "total": int(rows[0]["total_count"]) if rows else 0,
            "issue": issue, "reference": reference}
