"""The date the agent treats as today.

Imports nothing from this package. Both ``prompts`` and the table modules need
the working date, and ``prompts`` already imports from ``tables``, so a shared
module is what keeps the dependency acyclic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

DATA_LAG = timedelta(days=365)
"""How far the working date is set back from the wall clock.

Tonnage runs to 2026-07-20 and orders to 2026-01-06, both behind the real date, so
"today" against the wall clock matches nothing. A year back lands mid-dataset.
Remove once the pipeline feeds live positions.
"""


def working_date() -> date:
    """Return the date relative dates are resolved against.

    Returns
    -------
    date
        Today in UTC, shifted back by ``DATA_LAG``. Update dates are stored in
        UTC, so the working date follows them rather than the server clock.
    """
    return datetime.now(UTC).date() - DATA_LAG
