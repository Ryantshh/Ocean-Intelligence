"""Check the dashboard Change Feed (US-2.3) against independent answers.

Run from the repository root::

    .venv/Scripts/python.exe -m uat.run_feed_uat

The same rule as the chatbot UAT. The expected window bounds are worked out
here from the documented rule and the expected rows come from the
hand-written queries in ``expected_feed/``, all before the dashboard code is
imported. Only then is the dashboard's own ``change_feed`` called, the function
behind ``GET /api/dashboard/changes``, once per scenario with the working date
pinned to that scenario's day.

The feed is deterministic, so each scenario runs once. A list passes when
every row the dashboard shows is expected and every expected row is shown.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg

from uat.expected import _dsn

UAT_DIR = Path(__file__).resolve().parent
FEED_DIR = UAT_DIR / "expected_feed"
RESULTS_DIR = UAT_DIR / "results"

SCENARIOS = [
    ("2025-06-17", "dod"),
    ("2025-06-17", "wow"),
    ("2025-06-17", "24h"),
    ("2025-06-16", "dod"),
    ("2025-06-16", "wow"),
]
"""(working date, window). 17 June 2025 is a Tuesday and 16 June a Monday, the
day day-on-day reaches back to Friday."""

LISTS = {
    "vessel_status_changes": {
        "label": "Supply moves",
        "sql": "supply_moves.sql",
        "expected_key": ("vessel_id", "day", "direction"),
        "feed_key": ("vessel_id", "effective_date", "direction"),
        "params": ("since", "until", "now"),
    },
    "new_vessels": {
        "label": "New vessels",
        "sql": "new_vessels.sql",
        "expected_key": ("vessel_id", "day"),
        "feed_key": ("vessel_id", "new_as_of"),
        "params": ("since", "until", "now"),
    },
    "field_changes": {
        "label": "Field changes",
        "sql": "field_changes.sql",
        "expected_key": ("vessel_id", "update_date"),
        "feed_key": ("vessel_id", "update_date"),
        "params": ("since", "until"),
    },
    "new_orders": {
        "label": "New orders",
        "sql": "new_orders.sql",
        "expected_key": ("order_id", "order_seq"),
        "feed_key": ("order_id", "order_seq"),
        "params": ("since", "until"),
    },
}
"""Each Change Feed list, its expected query, the parameters it takes and the
columns that identify a row."""


def reference_now(working: date) -> datetime:
    """"Now" for a pinned working date: its last instant."""
    return datetime.combine(working + timedelta(days=1), datetime.min.time()) - timedelta(microseconds=1)


def window_bounds(working: date, window: str) -> tuple[datetime, datetime]:
    """The window the documentation describes, for a pinned working date.

    Day-on-day goes back to the same time on the previous weekday (Monday and
    Sunday reach back to Friday). Week-on-week is the seven whole days before
    the working date. 24h is the 24 hours before now.
    """
    now = reference_now(working)
    if window == "24h":
        return now - timedelta(hours=24), now
    if window == "wow":
        midnight = datetime.combine(working, datetime.min.time())
        return midnight - timedelta(days=7), midnight
    back = {0: 3, 6: 2}.get(working.weekday(), 1)
    return now - timedelta(days=back), now


def _normal(value: Any) -> str:
    """One spelling for a value from either side: dates to the second, ids as text."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        stamp = value.replace(tzinfo=None, microsecond=0)
        return stamp.date().isoformat() if stamp.time() == datetime.min.time() else stamp.isoformat(" ")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return _normal(datetime.strptime(text, pattern))
        except ValueError:
            continue
    return text


def _key(row: dict[str, Any], columns: tuple[str, ...]) -> str:
    return " | ".join(_normal(row.get(column)) for column in columns)


async def expected_rows() -> dict[str, Any]:
    """Every scenario's expected bounds and rows, from the database alone."""
    connection = await asyncpg.connect(_dsn(), statement_cache_size=0)
    try:
        results: dict[str, Any] = {}
        for working, window in SCENARIOS:
            since, until = window_bounds(date.fromisoformat(working), window)
            values = {"since": since, "until": until, "now": reference_now(date.fromisoformat(working))}
            scenario: dict[str, Any] = {"since": since.isoformat(" "), "until": until.isoformat(" "), "lists": {}}
            for key, spec in LISTS.items():
                sql = (FEED_DIR / spec["sql"]).read_text(encoding="utf-8")
                rows = await connection.fetch(sql, *(values[name] for name in spec["params"]))
                scenario["lists"][key] = [_key(dict(row), spec["expected_key"]) for row in rows]
            results[f"{working} {window}"] = scenario
        return results
    finally:
        await connection.close()


async def feed_rows() -> dict[str, Any]:
    """What the dashboard's Change Feed returns for every scenario."""
    from ai_platform.app.api import dashboard

    results: dict[str, Any] = {}
    for working, window in SCENARIOS:
        os.environ["OI_WORKING_DATE"] = working
        data = await dashboard.change_feed(window=window)
        results[f"{working} {window}"] = {
            "bounds": {k: data.get(k) for k in ("tonnage_since", "tonnage_until", "orders_since", "orders_until")},
            "lists": {key: [_key(row, spec["feed_key"]) for row in data.get(key, [])] for key, spec in LISTS.items()},
        }
    return results


def compare(expected: dict[str, Any], feed: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    for scenario, exp in expected.items():
        got = feed[scenario]
        bounds_ok = all(
            _normal(got["bounds"][f"{side}_since"]) == _normal(exp["since"])
            and _normal(got["bounds"][f"{side}_until"]) == _normal(exp["until"])
            for side in ("tonnage", "orders")
        )
        checks.append({"scenario": scenario, "list": "window", "label": "Window bounds", "passed": bounds_ok,
                       "expected": f"{exp['since']} to {exp['until']}",
                       "dashboard": f"{got['bounds']['tonnage_since']} to {got['bounds']['tonnage_until']}"})
        for key, spec in LISTS.items():
            want, have = Counter(exp["lists"][key]), Counter(got["lists"][key])
            missing = sorted((want - have).elements())
            extra = sorted((have - want).elements())
            checks.append({
                "scenario": scenario, "list": key, "label": spec["label"],
                "passed": not missing and not extra,
                "expected": sum(want.values()), "dashboard": sum(have.values()),
                "missing": missing, "extra": extra,
            })
    return checks


def write_report(out: Path, checks: list[dict[str, Any]]) -> None:
    lists = [c for c in checks if c["list"] != "window"]
    windows = [c for c in checks if c["list"] == "window"]
    lines = [
        f"# Change Feed UAT results — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        (
            f"Lists matching exactly: {sum(c['passed'] for c in lists)}/{len(lists)}. "
            f"Window bounds correct: {sum(c['passed'] for c in windows)}/{len(windows)}."
        ),
        "",
        "| Scenario | Check | Expected | Dashboard | Result |",
        "|---|---|---|---|---|",
    ]
    for c in checks:
        lines.append(f"| {c['scenario']} | {c['label']} | {c['expected']} | {c['dashboard']} | {'PASS' if c['passed'] else 'FAIL'} |")
    lines += ["", "## Differences", ""]
    for c in lists:
        if c["passed"]:
            continue
        lines += [f"### {c['scenario']} — {c['label']}", ""]
        if c["missing"]:
            lines.append(f"- Expected but not shown ({len(c['missing'])}): " + "; ".join(c["missing"][:15]))
        if c["extra"]:
            lines.append(f"- Shown but not expected ({len(c['extra'])}): " + "; ".join(c["extra"][:15]))
        lines.append("")
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")


async def main() -> None:
    out = RESULTS_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-feed")
    out.mkdir(parents=True)

    expected = await expected_rows()
    (out / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    print(f"Expected answers for {len(expected)} scenarios written to {out / 'expected.json'}")

    feed = await feed_rows()
    (out / "dashboard.json").write_text(json.dumps(feed, indent=2), encoding="utf-8")

    checks = compare(expected, feed)
    (out / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    write_report(out, checks)
    for c in checks:
        print(f"{c['scenario']:16} {c['label']:15} expected {c['expected']!s:>40}  dashboard {c['dashboard']!s:>40}  {'PASS' if c['passed'] else 'FAIL'}")
    print(f"Report: {out / 'summary.md'}")


if __name__ == "__main__":
    asyncio.run(main())
