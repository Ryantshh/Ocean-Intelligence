from datetime import date

from freight_ai.data.models import Intent, Order


def norm(value):
    return " ".join((value or "").casefold().split())


def query(records, intent: Intent):
    """Inclusive interval overlap; missing constrained values do not pass."""
    results = []
    for r in records:
        if intent.record_id and r.record_id != intent.record_id:
            continue
        if any(
            norm(getattr(r, key)) != norm(value)
            for key, value in intent.text_filters.items()
        ):
            continue
        lo, hi = (
            (r.cargo_weight_min, r.cargo_weight_max)
            if isinstance(r, Order)
            else (r.dwt, r.dwt)
        )
        start, end = (
            (r.laycan_start, r.laycan_end)
            if isinstance(r, Order)
            else (r.open_date_start, r.open_date_end)
        )
        if intent.min_tonnes is not None and (hi is None or hi < intent.min_tonnes):
            continue
        if intent.max_tonnes is not None and (lo is None or lo > intent.max_tonnes):
            continue
        if intent.window_start and (end is None or end < intent.window_start):
            continue
        if intent.window_end and (start is None or start > intent.window_end):
            continue
        results.append(r)
    results.sort(key=lambda r: r.record_id)
    result = {
        "total_count": len(results),
        "records": [r.model_dump(mode="json") for r in results[: intent.limit]],
        "truncated": len(results) > intent.limit,
        "aggregation": None,
        "missing_policy": "Missing constrained values excluded; records are snapshots, not unique business orders.",
    }
    if intent.aggregation == "count":
        result["aggregation"] = {"count": len(results)}
    elif intent.aggregation == "sum_tonnes":
        fields = (
            ["cargo_weight_min", "cargo_weight_max"]
            if intent.dataset == "orders"
            else ["dwt"]
        )
        result["aggregation"] = {
            f: {
                "known_sum": sum(
                    getattr(r, f) for r in results if getattr(r, f) is not None
                ),
                "missing_count": sum(getattr(r, f) is None for r in results),
            }
            for f in fields
        }
        result["aggregation"]["units"] = "metric tonnes; DWT includes fuel and stores"
    return result


def as_of_records(records, as_of: date):
    # Naive source timestamps interpreted as calendar dates; timezone is unknown.
    return [
        r
        for r in records
        if r.update_date is not None
        and r.date_received is not None
        and max(r.update_date.date(), r.date_received.date()) <= as_of
    ]


def latest_vessels(records, as_of: date):
    groups = {}
    for r in as_of_records(records, as_of):
        groups.setdefault(norm(r.vessel_name), []).append(r)
    latest, conflicts = [], set()
    for rows in groups.values():

        def timestamp(r):
            return (
                r.update_date,
                r.date_received,
                r.first_date_received or r.date_received,
            )

        rows.sort(key=lambda r: (timestamp(r), r.record_id), reverse=True)
        top = rows[0]
        ties = [r for r in rows if timestamp(r) == timestamp(top)]
        if len({r.record_id for r in ties}) > 1:
            conflicts.add(top.record_id)
        latest.append(top)
    return sorted(latest, key=lambda r: r.record_id), conflicts
