"""Node functions for the retrieval agent.

Each takes the state and returns only the keys it changes. Nothing here imports
chainlit; text reaches the UI through LangGraph's custom stream writer.

Writer payloads are tagged dicts rather than bare strings because two nodes now
stream. ``compact`` writes a summary that belongs in a progress step and
``answer`` writes the reply itself; untagged, the summary would land in the
reply.
"""

from __future__ import annotations

import json
from typing import Any, cast

import asyncpg
import openai
from langgraph.config import get_stream_writer
from openai.types.chat import ChatCompletionMessageParam
from pydantic import ValidationError

from ai_platform.backend.context import record_prompt_tokens, should_compact
from ai_platform.backend.db import fetch_rows
from ai_platform.backend.llm import get_client, get_model_name, stream_chat
from ai_platform.backend.prompts import (
    ANSWER_SYSTEM,
    COMPACTION_SYSTEM,
    DISCUSS_SYSTEM,
    EXTRACTION_SYSTEM,
)
from ai_platform.backend.state import AgentState
from ai_platform.backend.tables import (
    EXTRACTION_RESPONSE_FORMAT,
    Extraction,
    resolve,
)

KEEP_RECENT = 6
"""Messages left verbatim when compacting.

Three exchanges. A follow-up that refers back reaches one or two turns, not
twenty, so the recent window is what carries meaning and the rest is what the
summary is for.
"""


def _usage_dict(usage: Any) -> dict[str, int]:
    """Flatten a completion usage object into plain counts.

    Reasoning tokens are billed and occupy the window even though they are never
    displayed, so they are recorded separately rather than folded away.

    Parameters
    ----------
    usage : Any
        ``CompletionUsage`` from a response, or None.

    Returns
    -------
    dict of str to int
        Empty when the response carried no usage.
    """
    if usage is None:
        return {}
    details = usage.completion_tokens_details
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "reasoning_tokens": (details.reasoning_tokens or 0) if details else 0,
    }


async def compact(state: AgentState) -> dict[str, Any]:
    """Replace the older half of the conversation with a summary.

    Runs ahead of ``extract_filters`` because that is what consumes history, and
    only when the router says history has grown enough to be worth an extra
    model call.

    Parameters
    ----------
    state : AgentState
        Must carry ``history``.

    Returns
    -------
    dict
        ``history`` rewritten, plus ``summary`` and ``tokens``. Empty when there
        was nothing older than the recent window to compress.
    """
    history = state.get("history", [])
    older, recent = history[:-KEEP_RECENT], history[-KEEP_RECENT:]
    if not older:
        return {}

    writer = get_stream_writer()
    transcript = "\n\n".join(
        f"{message.get('role', '')}: {message.get('content', '')}" for message in older
    )

    usage: dict[str, int] = {}
    parts: list[str] = []
    async for token in stream_chat(
        [{"role": "user", "content": transcript}],
        system=COMPACTION_SYSTEM,
        usage=usage,
    ):
        parts.append(token)
        writer({"compact": token})

    summary = "".join(parts)
    return {
        "history": [{"role": "system", "content": summary}, *recent],
        "summary": summary,
        "tokens": usage,
    }


async def extract_filters(state: AgentState) -> dict[str, Any]:
    """Turn the question into a structured filter.

    The only point where untrusted output enters the pipeline, so every failure
    mode is handled here and nothing downstream needs to. Three exits, each
    setting a different key for the routing edge to read: ``filters`` on
    success, ``clarifying_question`` when the question carries nothing
    filterable, ``error`` when the response was unusable.

    Parameters
    ----------
    state : AgentState
        Must carry ``question``.

    Returns
    -------
    dict
        ``tokens``, plus exactly one of ``target`` and ``filters``,
        ``clarifying_question``, or ``error``.
    """
    history = state.get("history", [])
    messages = cast(
        "list[ChatCompletionMessageParam]",
        [
            {"role": "system", "content": EXTRACTION_SYSTEM},
            *history,
            {"role": "user", "content": state["question"]},
        ],
    )
    response = await get_client().chat.completions.create(
        model=get_model_name(),
        temperature=0,
        response_format=EXTRACTION_RESPONSE_FORMAT,
        messages=messages,
    )

    tokens = _usage_dict(response.usage)
    if response.usage is not None:
        record_prompt_tokens(response.usage.prompt_tokens, history)

    try:
        extraction = Extraction.model_validate_json(
            response.choices[0].message.content or "{}"
        )
    except ValidationError as exc:
        return {"tokens": tokens, "error": f"Could not read the question as a filter: {exc}"}

    if extraction.needs_clarification:
        return {
            "tokens": tokens,
            "clarifying_question": extraction.clarifying_question
            or "Which dates, size or id should I narrow to?",
        }

    return {
        "tokens": tokens,
        "target": extraction.request.target,
        "filters": extraction.request.filters,
    }


def build_query(state: AgentState) -> dict[str, Any]:
    """Compile the filter into parameterised SQL.

    Deterministic, and cannot fail on a validated filter: ``extract_filters``
    resolved the target through the registry before validating against that
    table's model, so both are known good by the time this runs.

    Parameters
    ----------
    state : AgentState
        Must carry ``target`` and ``filters``.

    Returns
    -------
    dict
        ``sql`` and ``params``.
    """
    filters = state.get("filters")
    assert filters is not None, "build_query runs only after extract_filters"
    sql, params = resolve(state.get("target", "")).build_sql(filters)
    return {"sql": sql, "params": params}


async def narrow(state: AgentState) -> dict[str, Any]:
    """Run the metadata filter and collect candidates.

    Stage one of retrieval. Ranking and fusion over these candidates arrive with
    the gold layer. There is no row cap, so ``rows`` is the complete match set
    and its length is the true count.

    Parameters
    ----------
    state : AgentState
        Must carry ``sql`` and ``params``.

    Returns
    -------
    dict
        ``rows``, or ``error`` when the query failed.
    """
    try:
        rows = await fetch_rows(state.get("sql", ""), state.get("params", []))
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        return {"error": f"Query failed: {exc}"}
    return {"rows": rows}


async def answer(state: AgentState) -> dict[str, Any]:
    """Write the reply, streaming tokens as they arrive.

    Three inputs, three shapes of reply. With rows, the model summarises them and
    every matching row is sent — a broad question therefore fails at the API
    rather than being answered from an arbitrary slice, which retrieval narrowing
    fixes and a row cap only hides.

    With no filters found, the message is answered from the conversation instead
    of the database. Extraction cannot tell a question about a previous answer
    from a search it cannot run, and reciting the filterable fields at both was
    useless for the first and only sometimes right for the second. Talking to
    both loses the crisp prompt for a date but costs no schema change.

    Parameters
    ----------
    state : AgentState
        Carries whichever of ``rows``, ``clarifying_question`` or ``error`` the
        earlier nodes produced.

    Returns
    -------
    dict
        ``tokens`` when the model ran, else empty. The reply itself reaches the
        caller through the stream writer.
    """
    writer = get_stream_writer()

    if state.get("clarifying_question"):
        usage: dict[str, int] = {}
        async for token in stream_chat(
            [
                *state.get("history", []),
                {"role": "user", "content": state["question"]},
            ],
            system=DISCUSS_SYSTEM,
            usage=usage,
        ):
            writer({"answer": token})
        return {"tokens": usage}

    failure = state.get("error")
    if failure:
        writer({"answer": f"I could not run that query. {failure}"})
        return {}

    rows = state.get("rows", [])
    filters = state.get("filters")
    context = {
        "table": state.get("target", "unknown"),
        "filters_applied": filters.model_dump(exclude_none=True) if filters else {},
        "row_count": len(rows),
        "rows": rows,
    }

    usage: dict[str, int] = {}
    try:
        async for token in stream_chat(
            [
                {
                    "role": "user",
                    "content": (
                        f"Question: {state['question']}\n\n"
                        f"Retrieved:\n{json.dumps(context, default=str)}"
                    ),
                }
            ],
            system=ANSWER_SYSTEM,
            usage=usage,
        ):
            writer({"answer": token})
    except openai.APIStatusError as exc:
        writer({"answer": _too_much_data(len(rows), exc)})
        return {}

    return {"tokens": usage}


def _too_much_data(row_count: int, exc: openai.APIStatusError) -> str:
    """Explain a result set the model could not be shown.

    Groq reports a context overflow as "reduce the length of the messages" with
    no numbers in it, so the row count is supplied here instead — it is the only
    figure the reader can act on. Without this the exception escapes the graph
    and the user sees a traceback rather than a sentence.

    Parameters
    ----------
    row_count : int
        Rows the query matched.
    exc : openai.APIStatusError
        The failure from the model API.

    Returns
    -------
    str
        A message naming the size of the result and what to do about it.
    """
    if "context_length" in str(exc.code or "") or "length" in exc.message.lower():
        return (
            f"That matched {row_count:,} rows — more than I can read in one go. "
            "The full set is in the table beside this. Narrowing the question "
            "with a tighter date range, a size bound or an id will let me "
            "summarise it."
        )
    return f"I could not answer from those {row_count:,} rows. {exc.message}"


def route_entry(state: AgentState) -> str:
    """Decide whether history needs compacting before anything else runs.

    Parameters
    ----------
    state : AgentState
        State at entry.

    Returns
    -------
    str
        Next node name.
    """
    if should_compact(state.get("history", [])):
        return "compact"
    return "extract_filters"


def route_after_extract(state: AgentState) -> str:
    """Decide whether to query or to ask a follow-up.

    Parameters
    ----------
    state : AgentState
        State after extraction.

    Returns
    -------
    str
        Next node name.
    """
    if state.get("error") or state.get("clarifying_question"):
        return "answer"
    return "build_query"
