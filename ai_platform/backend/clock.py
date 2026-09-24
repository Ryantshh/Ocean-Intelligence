"""The date the agent treats as today.

Imports nothing from this package. Both ``prompts`` and the table modules need
the working date, and ``prompts`` already imports from ``tables``, so a shared
module is what keeps the dependency acyclic.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

from dotenv import load_dotenv

load_dotenv()

DATA_LAG_YEARS = 1
"""How many calendar years the working date is set back from the wall clock.

Tonnage runs to 2026-07-20 and orders to 2026-01-06, both behind the real date, so
"today" against the wall clock matches nothing. A year back lands mid-dataset. A
calendar year rather than 365 days so the date agrees with the dashboard views,
which use ``now() - interval '1 year'``. Remove once the pipeline feeds live
positions.
"""

WORKING_DATE_OVERRIDE = "OI_WORKING_DATE"
"""Environment variable that pins the working date, as ``YYYY-MM-DD``, for testing.

Read once, when the table module is imported, so the app must be restarted after
changing it. Affects the chat agent only; the dashboard keeps the database clock.
"""


def overridden_working_date() -> date | None:
    """Return the pinned working date, when one is set.

    Returns
    -------
    date or None
        The date in ``OI_WORKING_DATE``, or None when the variable is unset or blank.

    Raises
    ------
    RuntimeError
        If the variable is set to something that is not an ISO date.
    """
    raw = os.environ.get(WORKING_DATE_OVERRIDE, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise RuntimeError(
            f"{WORKING_DATE_OVERRIDE} must be a date as YYYY-MM-DD, got {raw!r}"
        ) from error


def working_date() -> date:
    """Return the date relative dates are resolved against.

    Returns
    -------
    date
        The pinned date when ``OI_WORKING_DATE`` is set. Otherwise today in UTC,
        shifted back by ``DATA_LAG_YEARS``; a 29 February lands on 28 February, as
        Postgres does. Update dates are stored in UTC, so the working date follows
        them rather than the server clock.
    """
    pinned = overridden_working_date()
    if pinned is not None:
        return pinned
    today = datetime.now(UTC).date()
    target_year = today.year - DATA_LAG_YEARS
    is_leap_day = today.month == 2 and today.day == 29
    day = 28 if is_leap_day else today.day
    return today.replace(year=target_year, day=day)


def reference_now_sql() -> str:
    """Return the SQL expression for "now" used when judging a vessel's status.

    Returns
    -------
    str
        ``tonnage_reference_now()``, the dashboard's database clock, or a timestamp
        literal for midnight of the pinned date when ``OI_WORKING_DATE`` is set. The
        literal is built from a parsed ``date``, never from raw text.
    """
    pinned = overridden_working_date()
    if pinned is None:
        return "tonnage_reference_now()"
    return f"TIMESTAMP '{pinned.isoformat()} 00:00:00'"
