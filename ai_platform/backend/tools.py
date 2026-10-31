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
import json
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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
from ai_platform.backend.vocabulary import load as load_vocabulary

_logger = get_logger("agent")


class SearchRejected(ValueError):
    """A search refused before it runs, because it could not answer the question.

    Raised rather than returned so the tool-error path reports it: the run's
    evidence then records a failed search, not a search that found nothing.
    """


_STORED = {
    group: {name.lower(): name for name in names}
    for group, names in load_vocabulary().items()
}
"""Every stored name per vocabulary group, keyed by its lower-case spelling."""

PLACE_FIELDS = {
    "load_zone": ("zones", "load_port"),
    "discharge_parent_zone": ("zones", "discharge_port"),
    "parent_zone": ("zones", "open_area"),
    "load_port": ("ports", "load_zone"),
    "discharge_port": ("ports", "discharge_parent_zone"),
    "open_area": ("ports", "parent_zone"),
}
"""Each place field, the names it takes, and the field on the same table that
takes the other kind of place."""

NAME_FIELDS = {"cargo_type": "cargo_types", "vessel_status": "statuses"}
"""Single-name fields and the names each takes."""


def _check_search(table: str, search: BaseModel | None) -> list[str]:
    """Find what would make one table's search answer a different question.

    Two things do. A table object with no filter returns every row on the book,
    which overflows the model's context and answers nothing. A name that is not
    stored where it was put matches nothing, so a question whose answer is
    "nothing on the book" would look answered: North China searched as a zone,
    say, when it is stored only as a port label. Names that are stored are
    rewritten in their stored spelling, since zones match exactly.

    Parameters
    ----------
    table : str
        "cargoes" or "vessels", as the search names it.
    search : BaseModel or None
        That table's search, or None when it is not searched.

    Returns
    -------
    list of str
        One sentence per problem, written for the model; empty when the search
        can run.
    """
    if search is None:
        return []
    filters = {
        field: value
        for field, value in search.model_dump(exclude_none=True).items()
        if not isinstance(value, bool) and value != []
    }
    if not filters:
        return [
            (
                f"The {table} search sets no filter, so it would return every row on "
                f"the book. Leave {table} out, or set the filters the question needs."
            )
        ]

    problems = []
    for field, (group, other_field) in PLACE_FIELDS.items():
        values = filters.get(field)
        if not values:
            continue
        other_group = "ports" if group == "zones" else "zones"
        spelled = []
        for value in values:
            key = value.strip().lower()
            if key in _STORED[group]:
                spelled.append(_STORED[group][key])
            elif key in _STORED[other_group]:
                problems.append(
                    f'"{value}" is not a {group[:-1]}; it is stored as a '
                    f"{other_group[:-1]}, so it goes in {other_field}."
                )
            else:
                problems.append(
                    f'"{value}" is stored nowhere. Use the stored name for that '
                    "place, or ask the user which place they meant."
                )
        setattr(search, field, spelled)
    for field, group in NAME_FIELDS.items():
        value = filters.get(field)
        if not value:
            continue
        stored = _STORED[group].get(value.strip().lower())
        if stored is None:
            problems.append(
                f'"{value}" is not a stored {field.replace("_", " ")}. Use one '
                "from the list, or ask the user what they meant."
            )
        else:
            setattr(search, field, stored)
    return problems


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


STATUS_WORDS = re.compile(
    r"\b(open|free|fixed|fixtures?|subs|available|unfixed|status)\b", re.IGNORECASE
)
"""Words that ask for a fixture status. Whole words, so "openers" is not one."""

TIME_WORDS = re.compile(
    r"\b\d{4}-\d{2}(-\d{2})?\b|\b\d{1,2}/\d{1,2}\b|"
    r"\b(lists?|around|book|today|tonight|tomorrow|yesterday|now|days?|weeks?|"
    r"weekend|months?|years?|next|last|since|until|soon|recent|recently|latest|new|"
    r"fresh|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|"
    r"march|april|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)
"""Words that ask for a list or a time, either of which can bring a window.

A number counts only when it reads as a date, 2025-06-17 or 17/6: a size such as
180,000 dwt asks for no window. "5 June" and "in the last 3 days" are caught by
their month and day words.
"""

STATUS_REFUSAL = "The question asks for no fixture status, so leave commercial_status unset."
WINDOW_REFUSAL = "The question is not a list question and names no time"
"""How the two refusals open, so a repeat can be recognised in the conversation."""

LIVE_BOOK_HORIZON_DAYS = 30
"""How far ahead the live book's window opens, as the prompt's list rule sets it."""


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content or "")


def _answers_in(message: ToolMessage) -> dict[str, str]:
    """The question-to-answer map an ``ask_user`` result carries."""
    try:
        parsed = json.loads(_text_of(message.content))
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items() if key != ALREADY_ANSWERED_KEY}


def _since_question(messages: Sequence[Any]) -> list[Any]:
    """The messages from the trader's latest question on."""
    start = max(
        (index for index, message in enumerate(messages) if isinstance(message, HumanMessage)),
        default=0,
    )
    return list(messages[start:])


def _answered_questions(messages: Sequence[Any]) -> list[tuple[str, str, str]]:
    """Every form question answered since the trader's latest question.

    Returns
    -------
    list of tuple
        ``(header, question, answer)`` per answered question, oldest first.
    """
    recent = _since_question(messages)
    asked = {
        call["id"]: call["args"].get("questions", [])
        for message in recent
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call["name"] == "ask_user"
    }
    answered = []
    for message in recent:
        if not isinstance(message, ToolMessage) or message.tool_call_id not in asked:
            continue
        answers = _answers_in(message)
        for question in asked[message.tool_call_id]:
            text = question.get("question", "")
            if text in answers:
                answered.append((question.get("header", ""), text, answers[text]))
    return answered


def _trader_words(messages: Sequence[Any]) -> str:
    """What the trader has said about the question being answered.

    Their latest message and every answer they gave on a form since. The form's
    questions are left out, because the model wrote those.
    """
    recent = _since_question(messages)
    words = [_text_of(m.content) for m in recent if isinstance(m, HumanMessage)]
    words += [answer for _, _, answer in _answered_questions(messages)]
    return " ".join(words)


def _refused_before(messages: Sequence[Any]) -> set[str]:
    """Which unasked conditions were already refused since the trader's latest question."""
    refused = set()
    for message in _since_question(messages):
        if isinstance(message, ToolMessage):
            text = _text_of(message.content)
            if STATUS_REFUSAL in text:
                refused.add("status")
            if WINDOW_REFUSAL in text:
                refused.add("window")
    return refused


def _unasked_conditions(
    parsed: tables.Search, words: str, today: date, refused: set[str] = frozenset()
) -> tuple[list[str], list[str]]:
    """Conditions a search sets that nothing the trader said asks for.

    Two the model adds of its own accord: a fixture status, and the live book's
    window, the one a list question brings. Each is recognised exactly and acted
    on only when the trader's words hold no status word, or no list or time word,
    so a question that does ask for either is never touched. The matcher
    searches through its own tool, which sets OPEN by rule, and is not checked.

    The first time, the search is refused so the model can correct it. If the
    model sends the same unasked condition again, it is removed and the search
    runs: refusing again only loops until the run hits its call limit and the
    trader gets no answer at all.

    Parameters
    ----------
    parsed : tables.Search
        The search as the model sent it; a condition removed is cleared on it.
    words : str
        The trader's words, from :func:`_trader_words`.
    today : date
        The working date the live book's window starts on.
    refused : set of str
        "status" and/or "window", when already refused for this question.

    Returns
    -------
    tuple of list of str
        The refusals, one sentence each, written for the model; and what was
        removed instead, for the result to say.
    """
    problems: list[str] = []
    removed: list[str] = []
    vessels = parsed.vessels
    if vessels is not None and vessels.commercial_status and not STATUS_WORDS.search(words):
        if "status" in refused:
            removed.append(f"commercial_status {vessels.commercial_status}")
            vessels.commercial_status = None
        else:
            problems.append(STATUS_REFUSAL)
    horizon = today + timedelta(days=LIVE_BOOK_HORIZON_DAYS)
    windows = (
        ("cargoes", parsed.cargoes, "laycan_end_from", "laycan_start_to"),
        ("vessels", parsed.vessels, "open_end_from", "open_start_to"),
    )
    for table, search, end_field, start_field in windows:
        if search is None or TIME_WORDS.search(words):
            continue
        if getattr(search, end_field) == today and getattr(search, start_field) == horizon:
            if "window" in refused:
                removed.append(f"the {table} window ({end_field}, {start_field}, updated_from)")
                setattr(search, end_field, None)
                setattr(search, start_field, None)
                search.updated_from = None
            else:
                problems.append(
                    f"{WINDOW_REFUSAL}, so leave out the {table} window: "
                    f"{end_field}, {start_field} and updated_from."
                )
    return problems, removed


@tool("search_orders_and_tonnage")
async def search_orders_and_tonnage(
    runtime: ToolRuntime,
    cargoes: Annotated[
        tables.OrderSearch | None, Field(description="search cargo enquiries; omit to skip")
    ] = None,
    vessels: Annotated[
        tables.VesselSearch | None, Field(description="search vessel positions; omit to skip")
    ] = None,
) -> dict[str, Any]:
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

    Raises
    ------
    SearchRejected
        When a table is asked for with no filter, or a name is not stored where
        it was put. Nothing is searched; the message says what to change.
    """
    # arguments on the signature rather than args_schema=tables.Search, whose
    # extra="forbid" would reject the injected runtime (see match_orders)
    parsed = tables.Search(cargoes=cargoes, vessels=vessels)
    problems = _check_search("cargoes", parsed.cargoes) + _check_search(
        "vessels", parsed.vessels
    )
    messages = runtime.state["messages"]
    unasked, removed = _unasked_conditions(
        parsed, _trader_words(messages), working_date(), _refused_before(messages)
    )
    problems += unasked
    if problems:
        _logger.info("search rejected: %s", problems)
        raise SearchRejected(" ".join(problems))
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
    result = {
        "cargoes": cargoes,
        "vessels": vessels,
        "counts": {"cargoes": len(cargoes), "vessels": len(vessels)},
        "capped": capped,
    }
    if removed:
        _logger.info("search ran without unasked conditions: %s", removed)
        result["removed"] = (
            f"Searched without {' and '.join(removed)}: the question does not ask "
            "for them. Report these rows as the answer."
        )
    return result


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


ALREADY_ANSWERED_KEY = "note"
ALREADY_ANSWERED_NOTE = (
    "The questions above were answered earlier in this conversation; those answers "
    "are given back as they were. Use them as given and do not ask about them again."
)
"""Added to a result when some of its questions were answered before."""


def _earlier_answer(question: dict[str, Any], answered: list[tuple[str, str, str]]) -> str | None:
    """The answer the trader already gave to this question, if it is a repeat.

    A question repeats an earlier one when it carries the same header, or when its
    own wording names what the trader answered, as "Which port in North China?"
    does after the answer "North China".
    """
    header = question.get("header", "").strip().lower()
    wording = f"{question.get('question', '')} {header}".lower()
    for earlier_header, _, answer in answered:
        given = answer.strip()
        if not given:
            continue
        if earlier_header.strip().lower() == header or (len(given) >= 3 and given.lower() in wording):
            return given
    return None


@tool("ask_user")
def ask_user(
    questions: Annotated[
        list[Question], Field(min_length=1, max_length=4, description="one to four questions")
    ],
    runtime: ToolRuntime,
) -> dict[str, str]:
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
    # A question the trader already answered is answered again from that answer
    # rather than shown: the agent otherwise re-asks a settled place until the run
    # ends with no reply. Arguments are on the signature for the injected runtime,
    # as in search_orders_and_tonnage.
    parsed = AskUser(questions=questions)
    answered = _answered_questions(runtime.state["messages"])
    repeats: dict[str, str] = {}
    to_show = []
    for question in parsed.questions:
        shown = question.model_dump()
        shown["options"] = [
            option
            for option in shown["options"]
            if option["label"].strip().lower() not in OTHER_LABELS
        ]
        earlier = _earlier_answer(shown, answered)
        if earlier is None:
            to_show.append(shown)
        else:
            repeats[shown["question"]] = earlier
    if not repeats:
        _logger.info("ask_user: %s", [question["header"] for question in to_show])
        return interrupt({"questions": to_show})
    _logger.info("ask_user: already answered %s", list(repeats.values()))
    noted = {**repeats, ALREADY_ANSWERED_KEY: ALREADY_ANSWERED_NOTE}
    if not to_show:
        return noted
    _logger.info("ask_user: %s", [question["header"] for question in to_show])
    return {**noted, **interrupt({"questions": to_show})}


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
