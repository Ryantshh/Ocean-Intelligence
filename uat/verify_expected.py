"""Cross-check every expected chatbot answer with a second implementation.

Run from the repository root::

    .venv/Scripts/python.exe -m uat.verify_expected

The expected answers in ``expected/`` are SQL. This module fetches the raw rows
and applies each case's definition again in plain Python, with none of SQL's
NULL or date-casting rules, then compares the two row by row. Agreement shows
each query does what its comment says. It cannot show the definition is the
right reading of the question; that is reviewed in ``README.md``.

Imports nothing from ``ai_platform``, like ``expected.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg

from uat.expected import UAT_DIR, _dsn, compute, working_date


def _day(value: Any) -> date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def _zones(stored: str | None) -> list[str]:
    return [zone.strip().lower() for zone in stored.split(",")] if stored else []


def _has(label: str | None, name: str) -> bool:
    return bool(label) and name.lower() in label.lower()


def _place(zone: str | None, label: str | None, name: str) -> bool:
    """In a place: one of the stored zones, or named inside the stored label."""
    return name.lower() in _zones(zone) or _has(label, name)


def _within(day: date | None, first: date, last: date) -> bool:
    return day is not None and first <= day <= last


def build_checks(today: date) -> dict[str, Callable[[dict[str, Any]], bool]]:
    """Each case's definition as a test on one order, or on one vessel's latest report."""
    month_start, month_end = date(2025, 3, 1), date(2025, 3, 31)

    def on_book(o: dict[str, Any]) -> bool:
        return _day(o["date_received"]) <= today

    def todays_list(o: dict[str, Any]) -> bool:
        end, start, updated = _day(o["laycan_end"]), _day(o["laycan_start"]), _day(o["update_date"])
        return (end is not None and end >= today and start is not None
                and start <= today + timedelta(days=30) and updated is not None
                and updated >= today - timedelta(days=5))

    def vessel_list(v: dict[str, Any]) -> bool:
        end, start, updated = _day(v["open_date_end"]), _day(v["open_date_start"]), _day(v["update_date"])
        return (end is not None and end >= today and start is not None
                and start <= today + timedelta(days=30) and updated >= today - timedelta(days=5))

    def status_today(v: dict[str, Any]) -> str:
        covers = _within(today, _day(v["open_date_start"]) or date.max, _day(v["open_date_end"]) or date.min)
        return v["commercial_status"] if v["commercial_status"] in ("FIXED", "ON SUBS") and covers else "OPEN"

    def iron_ore(o: dict[str, Any]) -> bool:
        return _has(o["cargo_type"], "IRON ORE")

    return {
        "c01": lambda o: on_book(o) and _place(o["load_zone"], o["load_port"], "West Australia") and todays_list(o),
        "c02": lambda o: on_book(o) and _has(o["load_port"], "Tubarao") and _has(o["discharge_port"], "Qingdao")
        and iron_ore(o) and _day(o["update_date"]) >= today - timedelta(days=30),
        "c03": lambda o: on_book(o) and _place(o["load_zone"], o["load_port"], "East Coast South America"),
        "c04": lambda o: on_book(o) and _has(o["load_port"], "Madeira") and _has(o["discharge_port"], "North China"),
        "c05": lambda o: on_book(o) and todays_list(o),
        "c06": lambda o: on_book(o) and _place(o["load_zone"], o["load_port"], "West Australia")
        and _has(o["discharge_port"], "Qingdao"),
        "c08": lambda o: on_book(o) and _place(o["load_zone"], o["load_port"], "West Australia") and iron_ore(o)
        and _day(o["laycan_end"]) is not None and _day(o["laycan_end"]) >= month_start
        and _day(o["laycan_start"]) is not None and _day(o["laycan_start"]) <= month_end,
        "c09": lambda o: on_book(o) and _has(o["load_port"], "Tubarao")
        and o["cargo_weight_min"] is not None and o["cargo_weight_min"] >= 170000,
        "v01": lambda v: _place(v["parent_zone"], v["open_area"], "Pacific") and vessel_list(v),
        "v02": lambda v: _place(v["parent_zone"], v["open_area"], "East Coast South America")
        and v["ballast_laden"] == "BALLAST",
        "v03": lambda v: _place(v["parent_zone"], v["open_area"], "West Africa"),
        "v04": lambda v: _place(v["parent_zone"], v["open_area"], "Far East")
        and v["dwt"] is not None and v["dwt"] >= 180000 and status_today(v) == "OPEN",
        "v05": lambda v: status_today(v) == "FIXED",
        "v06": lambda v: _place(v["parent_zone"], v["open_area"], "West Australia")
        and _day(v["update_date"]) >= today - timedelta(days=3),
        "v08": lambda v: v["vessel_status"] == "Anchored",
        "v09": lambda v: _place(v["parent_zone"], v["open_area"], "Far East") and vessel_list(v),
    }


async def main() -> None:
    today = working_date()
    cases = json.loads((UAT_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
    sql_answers = await compute(cases, today)

    connection = await asyncpg.connect(_dsn(), statement_cache_size=0)
    try:
        orders = [dict(r) for r in await connection.fetch(
            "SELECT order_id::text AS order_id, date_received, update_date, laycan_start, laycan_end, "
            "load_port, discharge_port, cargo_type, load_zone, cargo_weight_min FROM order_test")]
        reports = [dict(r) for r in await connection.fetch(
            "SELECT vessel_id, update_date, first_date_received, parent_zone, open_area, dwt, commercial_status, "
            "open_date_start, open_date_end, vessel_status, ballast_laden FROM tonnage_test")]
    finally:
        await connection.close()

    known = [r for r in reports if r["update_date"] is not None and _day(r["update_date"]) <= today]
    latest: dict[str, dict[str, Any]] = {}
    for report in known:
        rank = (report["update_date"], report["first_date_received"] or datetime.min)
        current = latest.get(report["vessel_id"])
        if current is None or rank > (current["update_date"], current["first_date_received"] or datetime.min):
            latest[report["vessel_id"]] = report

    python_answers: dict[str, list[str]] = {}
    for case_id, check in build_checks(today).items():
        if case_id.startswith("c"):
            python_answers[case_id] = sorted(o["order_id"] for o in orders if check(o))
        else:
            python_answers[case_id] = sorted(v["vessel_id"] for v in latest.values() if check(v))
    python_answers["v07"] = sorted(r["vessel_id"] for r in known if r["vessel_id"] == "VESSEL 0743")

    agreed = 0
    for case_id in sorted(sql_answers):
        sql_ids = sorted(sql_answers[case_id]["ids"])
        py_ids = python_answers.get(case_id)
        same = py_ids == sql_ids
        agreed += same
        detail = "" if same else f"  only SQL {sorted(set(sql_ids) - set(py_ids or []))[:5]} only Python {sorted(set(py_ids or []) - set(sql_ids))[:5]}"
        print(f"{case_id}: SQL {len(sql_ids):>4}  Python {len(py_ids or []):>4}  {'AGREE' if same else 'DIFFER'}{detail}")
    print(f"{agreed}/{len(sql_answers)} expected answers agree with the second implementation")


if __name__ == "__main__":
    asyncio.run(main())
