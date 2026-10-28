"""What the agent can do.

The search tool embeds any free-text term, compiles the SQL and fetches the rows in
one function. Its docstring is what the model reads to choose it; the field-level
guidance — what each column accepts and the names it must be spelled with — lives
in the system prompt, structured per table, so it is written once.

``ask_user`` is a tool rather than middleware because the decision to ask belongs
to the model. It calls ``interrupt``, which saves the run and stops; the agent
resumes into the same call once the user submits.

``new_vessels`` answers the dashboard's New Vessels question with the dashboard's
own query, so the two can never disagree, and only over the last
``NEW_VESSEL_LOOKBACK_DAYS``. ``flag_for_dev_team`` records a question the desk's
data cannot answer, so declining it is never silent.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

from langchain_core.tools import tool
from langfuse import get_client
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

from ai_platform.backend import dashboard_queries, tables
from ai_platform.backend.clock import working_date
from ai_platform.backend.db import fetch_rows
from ai_platform.backend.embeddings import embed_search_terms
from ai_platform.backend.logging_utils import get_logger
from ai_platform.backend.sql import MAX_RANKED_ROWS

_logger = get_logger("agent")


async def _search(
    spec: tables.TableSpec, search: BaseModel | None
) -> list[dict[str, Any]]:
    """Embed any free-text terms, compile the SQL and fetch the rows.

    The search model is flat, so the split between exact filters and free-text
    terms happens here rather than being asked of the model: anything named in
    ``semantic_columns`` is embedded, everything else is read by ``build_sql``.

    Parameters
    ----------
    spec : TableSpec
        The table being searched.
    search : BaseModel or None
        That table's flat search model, or None to skip the table so both halves
        can be gathered unconditionally.

    Returns
    -------
    list of dict
        Matching rows, JSON-friendly. Empty when ``search`` is None.
    """
    if search is None:
        return []

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


@tool("search_orders_and_tonnage", args_schema=tables.Search)
async def search_orders_and_tonnage(**request: Any) -> dict[str, Any]:
    """Search cargo enquiries, vessel positions, or both at once.

    Set ``cargoes`` to search orders, ``vessels`` to search tonnage, or both when
    the question needs both — they run concurrently, so asking for both costs one
    round trip rather than two. Call this a second time only when the second
    search depends on what the first returned. To find vessels for orders, use
    match_orders instead.

    Read the rows before answering. If they do not match what was asked for — a
    load port you did not name, a region on the wrong continent — the search
    missed, and ask_user is the right next step rather than describing them.

    Every field, what it accepts and the names it must be spelled with are set
    out per table in the system prompt. Zones, statuses, cargo types and ports are
    matched by name; cargo_description is the only field searched by meaning.

    Returns
    -------
    dict
        ``cargoes`` and ``vessels``, each a list of rows; a search not asked for
        returns an empty list. ``counts`` holds the number of rows per table —
        use it for any count you report; never tally the rows yourself.
        ``capped`` names a table whose cargo_description search hit the fifty-row
        limit, meaning more matches exist than were returned — ask whether the
        user wants the closest fifty or every match, then re-run with
        ``exhaustive`` set. Name matches are never capped, however many rows.
    """
    parsed = tables.Search(**request)
    cargoes, vessels = await asyncio.gather(
        _search(tables.ORDERS, parsed.cargoes),
        _search(tables.TONNAGE, parsed.vessels),
    )
    capped = [
        name
        for name, rows, spec, search in (
            ("cargoes", cargoes, tables.ORDERS, parsed.cargoes),
            ("vessels", vessels, tables.TONNAGE, parsed.vessels),
        )
        if search is not None
        and not search.exhaustive
        and any(getattr(search, column, None) for column in spec.semantic_columns)
        and len(rows) >= MAX_RANKED_ROWS
    ]
    return {
        "cargoes": cargoes,
        "vessels": vessels,
        "counts": {"cargoes": len(cargoes), "vessels": len(vessels)},
        "capped": capped,
    }


OTHER_LABELS = frozenset({"other", "others", "something else", "none of these", "none"})


class Option(BaseModel):
    """One answer the user can pick."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="short text the user clicks, in plain words")
    description: str = Field(description="one line saying what picking it means")


class Question(BaseModel):
    """One question on the form, with its options."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(description="the full question; also the key in the answer")
    header: str = Field(description="chip of one or two words, twelve characters at most")
    options: list[Option] = Field(
        min_length=1,
        max_length=4,
        description=(
            "one to four real choices, best guess first; the form adds Other, so "
            "never invent a choice to fill a slot"
        ),
    )
    multi_select: bool | None = Field(
        default=None, description="true when more than one option may apply"
    )


class AskUser(BaseModel):
    """The form ``ask_user`` shows."""

    model_config = ConfigDict(extra="forbid")

    questions: list[Question] = Field(
        min_length=1, max_length=4, description="one to four questions"
    )


@tool("ask_user", args_schema=AskUser)
def ask_user(**request: Any) -> dict[str, str]:
    """Ask the user to settle something you cannot resolve.

    One to four questions, each with one to four real options — your best guess
    first, in plain words, never a column name, never invented to fill a slot.
    The form adds its own "Other" for free text, so an option named Other is
    dropped before the form is shown; never include one.

    Use it whenever a search field needs a value the user did not give: a vague
    time word, a place that could be a zone or a port, a bare month, a term you
    do not recognise, a search whose rows contradict the question.

    Returns
    -------
    dict of str to str
        Question text to the answer chosen or typed. Read it and fill the
        search fields yourself.
    """
    parsed = AskUser(**request)
    questions = []
    for question in parsed.questions:
        shown = question.model_dump()
        shown["options"] = [
            option
            for option in shown["options"]
            if option["label"].strip().lower() not in OTHER_LABELS
        ]
        questions.append(shown)
    _logger.info("ask_user: %s", [question["header"] for question in questions])
    return interrupt({"questions": questions})


NEW_VESSEL_LOOKBACK_DAYS = 14
"""How far back ``new_vessels`` answers, in days before today.

A vessel is new on the day it comes open while recently reported; past a fortnight
that is history rather than fresh supply, so older windows are declined and flagged.
"""

UNSUPPORTED_REQUEST_SCORE = "unsupported_request"
"""Langfuse score that marks a trace as a question the desk's data cannot answer.

A boolean score rather than a tag: tags are fixed when the trace starts, and a
score can be added from inside the run. Filter on it in Langfuse to find them.
"""


class NewVesselsWindow(BaseModel):
    """The days ``new_vessels`` counts."""

    model_config = ConfigDict(extra="forbid")

    since: date = Field(description="first day counted, inclusive")
    until: date | None = Field(
        default=None, description="last day counted, inclusive; leave null for today"
    )
    parent_zone: list[str] | None = Field(
        default=None,
        description="one or more zones from the zone list, stored spelling; a vessel in any of them matches",
    )
    dwt_min: float | None = Field(default=None, description="deadweight, lower bound")
    dwt_max: float | None = Field(default=None, description="deadweight, upper bound")


def _narrow_new_vessels(
    sql: str, params: list[Any], window: NewVesselsWindow
) -> tuple[str, list[Any]]:
    """Apply the zone and deadweight filters on top of the dashboard's query.

    The dashboard query is shared and stays untouched; it is wrapped instead, so
    the count is still the database's and the definition of new cannot drift.
    Zones match as the vessel search matches them: any element of the stored
    comma-separated list equal to any requested zone.

    Parameters
    ----------
    sql : str
        ``dashboard_queries.new_vessels_sql``'s statement.
    params : list
        Its parameters; the filters' are appended after them.
    window : NewVesselsWindow
        The request, carrying any filters.

    Returns
    -------
    tuple
        ``(sql, params)``, unchanged when no filter is set.
    """
    params = list(params)
    clauses = []
    if window.parent_zone:
        params.append(window.parent_zone)
        clauses.append(
            "EXISTS (SELECT 1 FROM unnest(string_to_array(nv.parent_zone, ', ')) AS zone "
            f"WHERE zone = ANY(${len(params)}::text[]))"
        )
    for bound, operator in (("dwt_min", ">="), ("dwt_max", "<=")):
        value = getattr(window, bound)
        if value is not None:
            params.append(value)
            clauses.append(f"nv.dwt {operator} ${len(params)}")
    if not clauses:
        return sql, params
    narrowed = (
        f"SELECT * FROM ({sql}) AS nv WHERE {' AND '.join(clauses)} "
        "ORDER BY nv.new_as_of DESC, nv.update_date DESC NULLS LAST"
    )
    return narrowed, params


@tool("new_vessels", args_schema=NewVesselsWindow)
async def new_vessels(**request: Any) -> dict[str, Any]:
    """Find vessels that came open as new supply between two days.

    A vessel is new on the day it comes OPEN while it has been reported in the
    five days before — the dashboard's New Vessels, counted the same way. Only
    the last fourteen days are covered. Narrow by zone and deadweight here,
    never by filtering the rows yourself, so the count stays exact.

    Returns
    -------
    dict
        ``vessels``, each at its latest position with ``new_as_of`` the day it
        came open; ``counts`` with the number of distinct vessels — use it for
        any count you report; ``by_day`` with distinct vessels per day. When the
        window reaches back too far, only ``out_of_range``, saying what to do.
    """
    parsed = NewVesselsWindow(**request)
    today = working_date()
    earliest = today - timedelta(days=NEW_VESSEL_LOOKBACK_DAYS)
    until = min(parsed.until or today, today)
    if parsed.since < earliest:
        return {
            "out_of_range": (
                f"New vessels are only covered from {earliest:%d %B %Y}, the last "
                f"{NEW_VESSEL_LOOKBACK_DAYS} days. Do not answer for the earlier days "
                "or estimate them: call flag_for_dev_team, then tell the user."
            )
        }
    if parsed.since > until:
        return {"out_of_range": f"The window starts after it ends, or after today ({today:%d %B %Y})."}

    # until is inclusive here and exclusive in the dashboard query
    sql, params = _narrow_new_vessels(
        *dashboard_queries.new_vessels_sql(
            datetime.combine(parsed.since, time()),
            datetime.combine(until + timedelta(days=1), time()),
        ),
        parsed,
    )
    # The dashboard view carries more than the vessel table shows, including an
    # interval that does not serialise; keep what the results panel displays.
    columns = (*tables.TONNAGE.display_columns, "new_as_of")
    rows = [
        {column: row.get(column) for column in columns}
        for row in await fetch_rows(sql, params)
    ]
    by_day = Counter(str(row.get("new_as_of")) for row in rows)
    _logger.info("new_vessels %s to %s -> %d rows", parsed.since, until, len(rows))
    return {
        "vessels": rows,
        "counts": {"vessels": len({row.get("vessel_id") for row in rows})},
        "by_day": dict(sorted(by_day.items())),
    }


class DevFlag(BaseModel):
    """A question the desk's data cannot answer."""

    model_config = ConfigDict(extra="forbid")

    request: str = Field(description="what the user asked for, in their words")
    reason: str = Field(description="why the desk's data cannot answer it, in one line")


@tool("flag_for_dev_team", args_schema=DevFlag)
def flag_for_dev_team(**request: Any) -> dict[str, bool]:
    """Pass a question the desk's data cannot answer to the dev team.

    Call it when the user asks about shipping or chartering but the book does not
    hold the answer — what a vessel actually carried, past fixtures, freight
    rates, owners or charterers, bunkers, congestion, news — or asks for new
    vessels further back than the last fourteen days. Never for small talk or
    nonsense; decline those without it.

    Returns
    -------
    dict
        ``flagged``, always true. Then tell the user it is not something you can
        answer from the desk's data yet and that it has gone to the dev team.
    """
    parsed = DevFlag(**request)
    _logger.warning("unsupported request flagged: %s (%s)", parsed.request, parsed.reason)
    try:
        get_client().score_current_trace(
            name=UNSUPPORTED_REQUEST_SCORE,
            value=1,
            data_type="BOOLEAN",
            comment=f"{parsed.request} | {parsed.reason}",
        )
    except Exception:
        # the warning above already records it; tracing must never fail a reply
        _logger.exception("could not score the trace for a flagged request")
    return {"flagged": True}
