"""What the agent can do.

The search tool embeds any free-text term, compiles the SQL and fetches the rows in
one function. Its docstring is what the model reads to choose it; the field-level
guidance — what each column accepts and the names it must be spelled with — lives
in the system prompt, structured per table, so it is written once.

``ask_user`` is a tool rather than middleware because the decision to ask belongs
to the model. It calls ``interrupt``, which saves the run and stops; the agent
resumes into the same call once the user submits.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

from ai_platform.backend import tables
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
    search depends on what the first returned, such as sizing vessels against the
    cargoes you just found.

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
