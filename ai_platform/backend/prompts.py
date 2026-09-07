"""The agent's system prompt, read from ``agent_system.md`` beside this module.

The markdown is a template: ``{date}`` and the four vocabulary lists —
``{zones}``, ``{ports}``, ``{cargo_types}``, ``{statuses}`` — are filled at import
from the working date and ``vocabulary.json``, so every name the prompt offers is
spelled as stored. The template may contain no braces beyond those five slots.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from ai_platform.backend.clock import working_date
from ai_platform.backend.vocabulary import load

PROMPT_PATH = Path(__file__).with_name("agent_system.md")

_VOCAB = load()


def _list(names: list[str]) -> str:
    """Join names for the prompt, wrapped and indented under a bullet.

    Parameters
    ----------
    names : list of str
        Names to list.

    Returns
    -------
    str
        The names separated by a middle dot, wrapped at eighty columns.
    """
    return textwrap.fill(
        " · ".join(names), width=80, initial_indent="  ", subsequent_indent="  "
    )


AGENT_SYSTEM = PROMPT_PATH.read_text(encoding="utf-8").format(
    date=f"{working_date():%A %d %B %Y}",
    zones=_list(_VOCAB["zones"]),
    ports=_list(_VOCAB["ports"]),
    cargo_types=_list(_VOCAB["cargo_types"]),
    statuses=_list(_VOCAB["statuses"]),
)
