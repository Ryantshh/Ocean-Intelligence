"""Match vessels to orders with one matcher agent per order.

``match_orders`` is the router: an ordinary tool on the main agent, written in
code. It reads each order's row from search results already in the agent's state,
checks every order is there, and runs one matcher agent per order in parallel, each
in a fresh context. The matcher builds its own vessel search from the row; code
writes each order's report from the rows it found. Only the reports return to the
main agent; the vessel rows go to the results panel through the custom stream.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from pydantic import ConfigDict, Field, create_model

from ai_platform.backend.llm import get_chat_model
from ai_platform.backend.logging_utils import get_logger
from ai_platform.backend.tables import TONNAGE, VesselSearch
from ai_platform.backend.tools import _search

_logger = get_logger("agent")

SEARCH_TOOL_NAME = "search_orders_and_tonnage"

MATCHER_TOOL_NAME = "search_open_vessels"

MATCHER_PROMPT = Path(__file__).with_name("matcher_agent.md").read_text(encoding="utf-8")
"""The matcher's own prompt: the matching rule for one order and nothing else."""

MAX_PARALLEL_MATCHERS = 5
"""Matchers running at once, against a data pool of six connections."""

MATCH_COLUMNS: tuple[str, ...] = (
    "order_id",
    *(column for column in TONNAGE.display_columns if column != "order_id"),
)
"""Columns of a match row in the results panel: the order matched, then the vessel.

The vessel's own ``order_id`` is dropped: those links are synthetic, and a second
``order_id`` column would read as the order the vessel was matched to.
"""


def _search_results(
    messages: Sequence[AnyMessage], tool_name: str = SEARCH_TOOL_NAME
) -> list[dict[str, Any]]:
    """Parse every result of one search tool in a message list, newest first.

    Parameters
    ----------
    messages : Sequence of AnyMessage
        An agent's message history.
    tool_name : str
        The tool whose results to read.

    Returns
    -------
    list of dict
        Parsed results of that tool; unparseable ones skipped.
    """
    parsed_results = []
    for message in reversed(messages):
        is_search_result = isinstance(message, ToolMessage) and message.name == tool_name
        if not is_search_result:
            continue
        try:
            parsed = json.loads(message.text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            parsed_results.append(parsed)
    return parsed_results


def _searched_order_ids(messages: Sequence[AnyMessage]) -> set[str]:
    """Collect every order id a cargo search in the history asked for by id.

    Parameters
    ----------
    messages : Sequence of AnyMessage
        An agent's message history.

    Returns
    -------
    set of str
        Ids named in ``cargoes.order_ids`` of any ``search_orders_and_tonnage`` call.
    """
    searched: set[str] = set()
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call["name"] != SEARCH_TOOL_NAME:
                continue
            cargoes = call["args"].get("cargoes") or {}
            searched.update(str(order_id) for order_id in cargoes.get("order_ids") or [])
    return searched


def order_rows_from_state(
    state: dict[str, Any], order_ids: Sequence[str]
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """Find each order's full row in search results already in the agent's state.

    An id that a cargo search already asked for by id, and that came back with no
    row, does not exist on the book; searching it again would find nothing.

    Parameters
    ----------
    state : dict
        The main agent's state; its ``messages`` hold every search result.
    order_ids : Sequence of str
        Orders asked for.

    Returns
    -------
    tuple
        ``(rows, missing, not_found)``: the newest row per found id; ids no search
        has returned or asked for; ids searched by id and not returned. Both lists
        keep the order asked, without repeats.
    """
    messages = state["messages"]
    wanted = set(order_ids)
    rows: dict[str, dict[str, Any]] = {}
    for result in _search_results(messages):
        for row in result.get("cargoes") or []:
            order_id = str(row.get("order_id"))
            if order_id in wanted and order_id not in rows:
                rows[order_id] = row
    searched = _searched_order_ids(messages)
    absent = list(dict.fromkeys(order_id for order_id in order_ids if order_id not in rows))
    missing = [order_id for order_id in absent if order_id not in searched]
    not_found = [order_id for order_id in absent if order_id in searched]
    return rows, missing, not_found


CONDITION_FIELDS = (
    "ballast_laden",
    "dwt_min",
    "dwt_max",
    "open_area",
    "vessel_status",
    "updated_from",
    "updated_to",
    "vessel_ids",
)
"""Vessel fields a trader may add on top of the matching rule.

Only fields that narrow a match without breaking it: the open-window bounds,
status and the history and future flags belong to the rule and are absent.
"""

VesselConditions = create_model(
    "VesselConditions",
    __config__=ConfigDict(extra="forbid"),
    **{name: (VesselSearch.model_fields[name].annotation, VesselSearch.model_fields[name]) for name in CONDITION_FIELDS},
)


@dataclass(frozen=True)
class MatcherContext:
    """What a matcher run receives besides its order.

    Attributes
    ----------
    conditions : dict
        The trader's vessel conditions, applied to the matcher's search in code.
    """

    conditions: dict[str, Any] = field(default_factory=dict)


@tool(MATCHER_TOOL_NAME)
async def search_open_vessels(
    parent_zone: Annotated[list[str], Field(min_length=1, description="the cargo's load zones")],
    open_end_from: Annotated[date, Field(description="the cargo's laycan start")],
    open_start_to: Annotated[date, Field(description="the cargo's laycan end")],
    runtime: ToolRuntime[MatcherContext],
    dwt_min: Annotated[float | None, Field(description="the cargo's minimum tonnes")] = None,
) -> dict[str, Any]:
    """Find the open vessels that can carry one cargo.

    Status is fixed to OPEN and the trader's conditions from the run context are
    added; the higher of the two minimum deadweights wins.

    Returns
    -------
    dict
        ``search``: the vessels search that ran, including any conditions the
        trader added. ``vessels``: every matching vessel. ``counts.vessels``: how
        many.
    """
    conditions = runtime.context.conditions
    minimums = [value for value in (dwt_min, conditions.get("dwt_min")) if value is not None]
    search = VesselSearch(
        **{**conditions, "dwt_min": max(minimums) if minimums else None},
        parent_zone=parent_zone,
        open_end_from=open_end_from,
        open_start_to=open_start_to,
        commercial_status="OPEN",
    )
    vessels = await _search(TONNAGE, search)
    return {
        "search": search.model_dump(mode="json", exclude_none=True),
        "vessels": vessels,
        "counts": {"vessels": len(vessels)},
    }


matcher_agent = create_agent(
    model=get_chat_model(),
    tools=[search_open_vessels],
    system_prompt=MATCHER_PROMPT,
    context_schema=MatcherContext,
    name="vessel_matcher",
)


def _report(order_id: str, rows: Sequence[dict[str, Any]]) -> str:
    """Write an order's report: the match count, then one bullet per vessel.

    Parameters
    ----------
    order_id : str
        The order matched.
    rows : Sequence of dict
        Its matched vessels, earliest open date first.

    Returns
    -------
    str
        ``- VESSEL 0887 · 183k dwt · laden · Singapore · open 2025-01-01 → 2025-01-03``
        per vessel under a count line.
    """
    if not rows:
        return f"{order_id}: no open vessel fits this order."
    lines = [f"{order_id}: {len(rows)} vessels matched."]
    lines += [
        f"- {row.get('vessel_id')} · {round((row.get('dwt') or 0) / 1000)}k dwt · "
        f"{str(row.get('ballast_laden') or 'unknown').lower()} · "
        f"{row.get('open_area') or 'unknown area'} · "
        f"open {str(row.get('open_date_start'))[:10]} → {str(row.get('open_date_end'))[:10]}"
        for row in rows
    ]
    return "\n".join(lines)


def _last_search(messages: Sequence[AnyMessage]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read a matcher's last search: what ran, and its vessels earliest open first.

    Parameters
    ----------
    messages : Sequence of AnyMessage
        The matcher's final message history.

    Returns
    -------
    tuple
        ``(search, rows)``: the vessels search that ran, and its rows; both empty
        when the matcher never searched.
    """
    results = _search_results(messages, MATCHER_TOOL_NAME)
    if not results:
        return {}, []
    rows = sorted(
        results[0].get("vessels", []),
        key=lambda row: (str(row.get("open_date_start") or ""), str(row.get("vessel_id"))),
    )
    return results[0].get("search", {}), rows


async def _match_one(
    number: int,
    order_id: str,
    row: dict[str, Any],
    conditions: dict[str, Any],
    limit: asyncio.Semaphore,
    write: Callable[[Any], None],
) -> dict[str, Any]:
    """Run one matcher for one order.

    A failure is reported for that order rather than raised, so one failed order
    does not discard the others.

    Parameters
    ----------
    number : int
        The matcher's number within this call, from 1, shown on its progress step.
    order_id : str
        The order to match.
    row : dict
        Its full cargo row.
    conditions : dict
        The trader's vessel conditions, applied to its search in code.
    limit : asyncio.Semaphore
        Shared cap on matchers running at once.
    write : Callable
        The tool's stream writer, for the progress events.

    Returns
    -------
    dict
        Order id, report and vessel rows.
    """
    async with limit:
        write({"kind": "match_started", "order_id": order_id, "agent": number})
        search: dict[str, Any] = {}
        try:
            final_state = await matcher_agent.ainvoke(
                {"messages": [HumanMessage(f"Order {order_id}.\n\n{json.dumps(row, indent=1)}")]},
                context=MatcherContext(conditions=conditions),
            )
            search, rows = _last_search(final_state["messages"])
            report = _report(order_id, rows)
        except Exception as error:
            _logger.exception("matcher failed for order %s", order_id)
            report, rows = f"The vessel search for order {order_id} failed: {error}", []
        write(
            {
                "kind": "match_finished",
                "order_id": order_id,
                "agent": number,
                "matched": len(rows),
                "search": {"vessels": search},
            }
        )
    return {"order_id": order_id, "report": report, "rows": rows}


def _panel_rows(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag every matched vessel row with the order it was matched to.

    Parameters
    ----------
    results : Sequence of dict
        Matcher results in order.

    Returns
    -------
    list of dict
        Rows keyed by ``MATCH_COLUMNS``; the vessel's own synthetic ``order_id`` is
        replaced by the matched order's.
    """
    return [
        {
            column: result["order_id"] if column == "order_id" else row.get(column)
            for column in MATCH_COLUMNS
        }
        for result in results
        for row in result["rows"]
    ]


@tool("match_orders")
async def match_orders(
    order_ids: Annotated[list[str], Field(min_length=1, description="order numbers exactly as shown")],
    runtime: ToolRuntime,
    vessel_conditions: VesselConditions | None = None,
) -> dict[str, Any]:
    """Find open vessels for two or more orders, one matcher per order.

    Use this when the user asks which vessels could cover, fit, lift or take
    several orders; for a single order, run the vessels search yourself with the
    matching rule. Pass the order numbers. Every order must already be in a search
    result of this conversation: if any is not, nothing is matched and
    ``not_in_results`` lists them — search those order numbers, then call this
    again with the same numbers. Never call it in the same step as that search.

    ``vessel_conditions``: only vessel conditions the user stated, such as
    ballasters only or at least 180,000 dwt; they apply to every order. Leave it
    unset otherwise. Cargo conditions such as cargo type choose which orders to
    pass, not vessel conditions.

    Arguments are declared on the signature, not an ``args_schema`` model, whose
    ``extra="forbid"`` would reject the injected ``runtime``.

    Returns
    -------
    dict
        ``orders``: per order, ``matched`` (vessel count; report this, never a
        tally) and ``report`` (one bullet per matched vessel). ``not_found``: orders
        searched by number that do not exist. ``cannot_match``: orders lacking a
        load zone or laycan. ``not_in_results``: orders not yet in any search
        result; when set, ``orders`` is empty.
    """
    rows, missing, not_found = order_rows_from_state(runtime.state, order_ids)
    if missing:
        return {"orders": [], "not_found": not_found, "cannot_match": [], "not_in_results": missing}

    found_ids = [order_id for order_id in dict.fromkeys(order_ids) if order_id in rows]
    cannot_match = [
        order_id
        for order_id in found_ids
        if not (rows[order_id].get("load_zone") and rows[order_id].get("laycan_start") and rows[order_id].get("laycan_end"))
    ]
    matchable_ids = [order_id for order_id in found_ids if order_id not in cannot_match]
    conditions = vessel_conditions.model_dump(exclude_none=True) if vessel_conditions else {}
    limit = asyncio.Semaphore(MAX_PARALLEL_MATCHERS)
    results = await asyncio.gather(
        *(
            _match_one(number, order_id, rows[order_id], conditions, limit, runtime.stream_writer)
            for number, order_id in enumerate(matchable_ids, start=1)
        )
    )

    panel_rows = _panel_rows(results)
    if panel_rows:
        runtime.stream_writer({"kind": "match_rows", "rows": panel_rows})
    return {
        "orders": [
            {"order_id": result["order_id"], "matched": len(result["rows"]), "report": result["report"]}
            for result in results
        ],
        "not_found": not_found,
        "cannot_match": cannot_match,
        "not_in_results": [],
    }
