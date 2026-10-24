"""Each trader's saved voyage calculator defaults, in ``public.voyage_defaults``.

The table is created by ``infra/sql/voyage_defaults_setup.sql``.
"""

from __future__ import annotations

import json

from ai_platform.backend.db import fetch_rows


async def load_voyage_defaults(user_identifier: str | None) -> dict[str, float]:
    """Read one trader's saved defaults.

    Parameters
    ----------
    user_identifier : str or None
        The trader's login identifier.

    Returns
    -------
    dict of str to float
        Input name to saved value; empty when nothing is saved or no one is
        logged in.
    """
    if not user_identifier:
        return {}
    rows = await fetch_rows(
        "SELECT input_values FROM public.voyage_defaults WHERE user_identifier = $1",
        [user_identifier],
    )
    if not rows:
        return {}
    stored = rows[0]["input_values"]
    return json.loads(stored) if isinstance(stored, str) else dict(stored)


async def save_voyage_defaults(user_identifier: str, input_values: dict[str, float]) -> None:
    """Replace one trader's saved defaults.

    Parameters
    ----------
    user_identifier : str
        The trader's login identifier.
    input_values : dict of str to float
        Input name to value, already validated.
    """
    await fetch_rows(
        """
        INSERT INTO public.voyage_defaults (user_identifier, input_values, updated_at)
        VALUES ($1, $2::jsonb, now())
        ON CONFLICT (user_identifier)
        DO UPDATE SET input_values = EXCLUDED.input_values, updated_at = now()
        """,
        [user_identifier, json.dumps(input_values)],
    )
