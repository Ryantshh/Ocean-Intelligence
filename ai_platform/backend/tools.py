"""What the agent can do.

Each search tool is the old embed, build_query and narrow nodes collapsed into
one function. The docstring is what the model reads to choose a tool and fill its
arguments, so the field guidance lives there rather than in a system prompt.

``ask_user`` is a tool rather than middleware because the decision to ask belongs
to the model. It calls ``interrupt``, which saves the run and stops; the agent
resumes into the same call once the user submits.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel

from ai_platform.backend import tables
from ai_platform.backend.db import fetch_rows
from ai_platform.backend.embeddings import embed_search_terms
from ai_platform.backend.logging_utils import get_logger

_logger = get_logger("agent")


async def _search(
    spec: tables.TableSpec, search: BaseModel
) -> list[dict[str, Any]]:
    """Embed any free-text terms, compile the SQL and fetch the rows.

    The search model is flat, so the split between exact filters and free-text
    terms happens here rather than being asked of the model: anything named in
    ``semantic_columns`` is embedded, everything else is read by ``build_sql``.

    Parameters
    ----------
    spec : TableSpec
        The table being searched.
    search : BaseModel
        That table's flat search model.

    Returns
    -------
    list of dict
        Matching rows, JSON-friendly.
    """
    filled = search.model_dump(exclude_none=True)
    terms = {
        field: value
        for field, value in filled.items()
        if field in spec.semantic_columns and value
    }

    vectors: Sequence[tuple[str, Sequence[float]]] = ()
    if terms:
        term_fields = list(terms)
        embedded = await embed_search_terms([terms[f] for f in term_fields])
        vectors = list(zip(term_fields, embedded, strict=True))

    sql, params = spec.build_sql(search, vectors)
    rows = await fetch_rows(sql, params)
    _logger.info(
        "search %s: %s -> %d rows",
        spec.table,
        {k: v for k, v in filled.items() if v not in ([], False)},
        len(rows),
    )
    return rows


@tool("search_orders", args_schema=tables.OrderSearch)
async def search_orders(**search: Any) -> list[dict[str, Any]]:
    """Search cargo enquiries. Returns every matching row.

    Read the rows before answering. If they do not match what was asked for — a
    load port you did not name, a region on the wrong continent — the search
    missed, and ask_user is the right next step rather than describing them.

    All fields are optional and flat — there is no nesting. Filter fields:
      order_ids         list of integers, only when the user quotes order numbers
      laycan_start_from ISO date, laycan first day on or after
      laycan_start_to   ISO date, laycan first day on or before
      laycan_end_from   ISO date, laycan cancels on or after, still open then
      laycan_end_to     ISO date, laycan cancels on or before, must be fixed by then
      received_from     ISO date, when the enquiry arrived, on or after
      received_to       ISO date, when the enquiry arrived, on or before
      updated_from      ISO date, last amended on or after
      updated_to        ISO date, last amended on or before
      weight_min        cargo tonnes, floor. The stem's smallest size must reach
                        it, so 153,000-187,000 does not answer "at least 160,000"
      weight_max        cargo tonnes, ceiling. The largest size must fit under it
      include_future    true only for upcoming or forward-dated records

    A month means the laycan OVERLAPS that month, which takes two fields on
    opposite ends: laycan_end_from = the 1st, laycan_start_to = the last day.
    Setting both bounds on the same end is the narrower question of when a window
    begins or ends.

    Semantic fields, matched by meaning rather than exact text:
      cargo_type              commodity, e.g. iron ore, coal, bauxite
      cargo_description       wording from the enquiry itself
      load_port               load port, terminal or country
      load_zone               broad load region, from the zone list only
      discharge_port          discharge port, terminal or country
      discharge_parent_zone   broad discharge region, from the zone list only
    """
    return await _search(tables.ORDERS, tables.OrderSearch(**search))


@tool("search_tonnage", args_schema=tables.VesselSearch)
async def search_tonnage(**search: Any) -> list[dict[str, Any]]:
    """Search vessel positions. Returns every matching row.

    Read the rows before answering. If they do not match what was asked for, the
    search missed, and ask_user is the right next step.

    Each vessel appears once, at its most recent report. The table holds 11,105
    reports over 1,037 vessels, so without that a ship would appear many times.

    All fields are optional and flat — there is no nesting. Filter fields:
      vessel_ids        list of strings, verbatim including the prefix, e.g.
                        "VESSEL 0001". Never strip VESSEL or drop leading zeros
      open_start_from   ISO date, first free date on or after
      open_start_to     ISO date, first free date on or before
      open_end_from     ISO date, window closes on or after, still open then
      open_end_to       ISO date, window closes on or before, must be fixed by then
      updated_from      ISO date, position last updated on or after
      updated_to        ISO date, position last updated on or before
      received_from     ISO date, position first reported on or after
      received_to       ISO date, position first reported on or before
      dwt_min           deadweight tonnes, lower bound
      dwt_max           deadweight tonnes, upper bound
      ballast_laden     LADEN or BALLAST
      commercial_status FIXED, ON SUBS, or AVAILABLE. Unfixed vessels are AVAILABLE
      include_history   true only for past positions or how a vessel has moved
      include_future    true only for upcoming or forward-dated records

    A month means the open window OVERLAPS it: open_end_from = the 1st,
    open_start_to = the last day.

    Every vessel here is Capesize, dwt 160,000 to 190,000. Ship type and ship
    size cannot be filtered or searched at all.

    Semantic fields, matched by meaning rather than exact text:
      vessel_status     navigational status, from the status list only
      open_area         specific open area, port or country
      parent_zone       broad region containing it, from the zone list only
    """
    return await _search(tables.TONNAGE, tables.VesselSearch(**search))


@tool
def ask_user(reason: str, fields: dict[str, str]) -> dict[str, str]:
    """Ask the user to fill in values you could not resolve.

    Call this when a search came back with rows that plainly do not match what
    was asked for, when a place could be either a zone or a port, or when a term
    is shorthand you cannot expand. For a single missing value, ask in your reply
    instead — this is for when several fields need filling at once.

    Parameters
    ----------
    reason : str
        One line the user reads above the form. Say what could not be resolved.
    fields : dict of str to str
        Field name to pre-filled value. Use the field names from the search
        tools, and an empty string where you have no guess.

    Returns
    -------
    dict of str to str
        What the user submitted, ready to pass to a search tool.
    """
    _logger.info("ask_user: %s fields=%s", reason, list(fields))
    return interrupt({"reason": reason, "fields": fields})
