"""The date the agent treats as today.

Imports nothing from this package. Both ``prompts`` and the table modules need
the working date, and ``prompts`` already imports from ``tables``, so a shared
module is what keeps the dependency acyclic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

DATA_LAG_YEARS = 1
"""How many calendar years the working date is set back from the wall clock.

Tonnage runs to 2026-07-20 and orders to 2026-01-06, both behind the real date, so
"today" against the wall clock matches nothing. A year back lands mid-dataset. A
calendar year rather than 365 days so the date agrees with the dashboard views,
which use ``now() - interval '1 year'``. Remove once the pipeline feeds live
positions.
"""


def working_date() -> date:
    """Return the date relative dates are resolved against.

    Returns
    -------
    date
        Today in UTC, shifted back by ``DATA_LAG_YEARS``. A 29 February lands on
        28 February, as Postgres does. Update dates are stored in UTC, so the
        working date follows them rather than the server clock.
    """
    today = datetime.now(UTC).date()
    target_year = today.year - DATA_LAG_YEARS
    is_leap_day = today.month == 2 and today.day == 29
    day = 28 if is_leap_day else today.day
    return today.replace(year=target_year, day=day)
