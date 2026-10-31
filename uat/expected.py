"""Expected answers for the chatbot UAT, computed straight from the database.

Imports nothing from ``ai_platform``, on purpose. Each expected answer is a
hand-written query in ``expected/``, written from the table schema, the data
glossary and what a trader means by the question, never from the agent's query
builder. Sharing no code with the agent is what lets a defect in the builder
show up as a mismatch instead of being copied into the answer it is checked
against. The connection is opened here with asyncpg for the same reason.

Every query takes one parameter, ``$1``, the working date, and returns one
column, ``id``: the order id as text for cargoes, the vessel id for vessels.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

load_dotenv()

UAT_DIR = Path(__file__).resolve().parent
EXPECTED_DIR = UAT_DIR / "expected"


def working_date() -> date:
    """Read the pinned working date the expected answers are computed for.

    Returns
    -------
    date
        The date in ``OI_WORKING_DATE``.

    Raises
    ------
    RuntimeError
        If the variable is unset: an unpinned run compares answers for a moving
        "today" and cannot be reproduced.
    """
    raw = os.environ.get("OI_WORKING_DATE", "").strip()
    if not raw:
        raise RuntimeError("Set OI_WORKING_DATE in .env before running the UAT.")
    return date.fromisoformat(raw)


def _dsn() -> str:
    url = (
        os.environ.get("DATA_DATABASE_URL", "").strip()
        or os.environ.get("CHAINLIT_DATABASE_URL", "").strip()
    )
    if not url:
        raise RuntimeError("DATA_DATABASE_URL and CHAINLIT_DATABASE_URL are unset.")
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def compute(cases: list[dict[str, Any]], today: date) -> dict[str, dict[str, Any]]:
    """Run the expected query of every case that has one.

    Parameters
    ----------
    cases : list of dict
        Case definitions from ``cases.json``.
    today : date
        Working date passed to each query as ``$1``.

    Returns
    -------
    dict
        Case id to ``{"sql", "ids", "count"}``.
    """
    connection = await asyncpg.connect(_dsn(), statement_cache_size=0)
    try:
        results: dict[str, dict[str, Any]] = {}
        for case in cases:
            if not case.get("expected"):
                continue
            sql = (EXPECTED_DIR / case["expected"]).read_text(encoding="utf-8")
            rows = await connection.fetch(sql, today)
            ids = [str(row["id"]) for row in rows]
            results[case["id"]] = {"sql": sql, "ids": ids, "count": len(ids)}
        return results
    finally:
        await connection.close()


async def stored_names() -> dict[str, list[str]]:
    """Every name stored in the data, for checking what the agent searched for.

    A search for a name that is stored nowhere returns nothing whatever the
    question, so a case whose right answer is "nothing" would pass on it. Zones
    are the comma-separated elements of every zone column, both tables; port
    labels are the whole stored labels of every port and area column, since a
    port name matches inside a longer label.

    Returns
    -------
    dict
        ``zones``, ``port_labels``, ``cargo_types`` and ``statuses``, lower-case.
    """
    connection = await asyncpg.connect(_dsn(), statement_cache_size=0)
    try:
        queries = {
            "zones": (
                "SELECT DISTINCT lower(regexp_split_to_table(z, '\\s*,\\s*')) AS name FROM ("
                " SELECT load_zone AS z FROM order_test"
                " UNION ALL SELECT discharge_parent_zone FROM order_test"
                " UNION ALL SELECT parent_zone FROM tonnage_test) AS zones WHERE z IS NOT NULL"
            ),
            "port_labels": (
                "SELECT DISTINCT lower(l) AS name FROM ("
                " SELECT load_port AS l FROM order_test"
                " UNION ALL SELECT discharge_port FROM order_test"
                " UNION ALL SELECT open_area FROM tonnage_test) AS labels WHERE l IS NOT NULL"
            ),
            "cargo_types": "SELECT DISTINCT lower(cargo_type) AS name FROM order_test WHERE cargo_type IS NOT NULL",
            "statuses": "SELECT DISTINCT lower(vessel_status) AS name FROM tonnage_test WHERE vessel_status IS NOT NULL",
        }
        return {key: [row["name"] for row in await connection.fetch(sql)] for key, sql in queries.items()}
    finally:
        await connection.close()


if __name__ == "__main__":
    import json

    all_cases = json.loads((UAT_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
    pinned = working_date()
    for case_id, result in asyncio.run(compute(all_cases, pinned)).items():
        print(f"{case_id}: {result['count']}")
