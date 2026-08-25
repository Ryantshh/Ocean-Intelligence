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
import httpx
import openai
from langgraph.config import get_stream_writer
from openai.types.chat import ChatCompletionMessageParam
from pydantic import ValidationError

from ai_platform.backend.context import record_prompt_tokens, should_compact
from ai_platform.backend.db import fetch_rows
from ai_platform.backend.embeddings import embed_search_terms
from ai_platform.backend.llm import (
    get_client,
    get_model_name,
    stream_chat,
    trace_kwargs,
)
from ai_platform.backend.logging_utils import get_logger
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
    resolve_table,
)

_logger = get_logger("agent")

KEEP_RECENT_MESSAGES = 6
"""Messages left verbatim when compacting.

Three exchanges. A follow-up that refers back reaches one or two turns, not
twenty, so the recent window is what carries meaning and the rest is what the
summary is for.
"""


def _token_counts(usage: Any) -> dict[str, int]:
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
    to_summarise, to_keep = (
        history[:-KEEP_RECENT_MESSAGES],
        history[-KEEP_RECENT_MESSAGES:],
    )
    if not to_summarise:
        return {}

    _logger.info("compact: summarising %d of %d messages", len(to_summarise), len(history))
    writer = get_stream_writer()
    transcript = "\n\n".join(
        f"{message.get('role', '')}: {message.get('content', '')}"
        for message in to_summarise
    )

    usage: dict[str, int] = {}
    summary_parts: list[str] = []
    async for token in stream_chat(
        [{"role": "user", "content": transcript}],
        system=COMPACTION_SYSTEM,
        usage=usage,
    ):
        summary_parts.append(token)
        writer({"compact": token})

    summary = "".join(summary_parts)
    return {
        "history": [{"role": "system", "content": summary}, *to_keep],
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
    _logger.info("extract_filters: question=%r", state["question"])
    # history is already compacted by this point if it needed to be
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
        **trace_kwargs(),
    )

    tokens = _token_counts(response.usage)
    if response.usage is not None:
        record_prompt_tokens(response.usage.prompt_tokens, history)

    # three exits from here, one per key the routing edge reads
    try:
        extraction = Extraction.model_validate_json(
            response.choices[0].message.content or "{}"
        )
    except ValidationError as exc:
        _logger.warning("extract_filters: could not parse extraction: %s", exc)
        return {"tokens": tokens, "error": f"Could not read the question as a filter: {exc}"}

    if extraction.needs_clarification:
        _logger.info("extract_filters: needs clarification")
        return {
            "tokens": tokens,
            "clarifying_question": extraction.clarifying_question
            or "Which dates, size or id should I narrow to?",
        }

    _logger.info(
        "extract_filters: target=%s filters=%s",
        extraction.request.target,
        extraction.request.filters.model_dump(exclude_none=True),
    )
    return {
        "tokens": tokens,
        "target": extraction.request.target,
        "filters": extraction.request.filters,
        "semantic": extraction.request.semantic.model_dump(exclude_none=True),
    }


async def embed(state: AgentState) -> dict[str, Any]:
    """Turn the extracted free-text terms into query vectors.

    One call for every term, since there are at most six. Cohere's token count is
    not recorded: it is a separate provider with its own window, and folding it
    into ``tokens`` would misreport how full the chat context is.

    Parameters
    ----------
    state : AgentState
        Must carry ``semantic``.

    Returns
    -------
    dict
        ``vectors`` as ``(field, embedding)`` pairs, or ``error`` when the
        embedding call failed. Empty when no term was set.
    """
    search_terms = {
        field: term for field, term in state.get("semantic", {}).items() if term
    }
    if not search_terms:
        return {}

    # field order is fixed here so the vectors can be zipped back onto it
    term_fields = list(search_terms)
    try:
        term_vectors = await embed_search_terms(
            [search_terms[field] for field in term_fields]
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        _logger.warning("embed: failed for fields %s: %s", term_fields, exc)
        return {"error": f"Could not embed the search terms: {exc}"}
    return {"vectors": list(zip(term_fields, term_vectors, strict=True))}


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
    sql, params = resolve_table(state.get("target", "")).build_sql(
        filters, state.get("vectors", [])
    )
    _logger.debug("build_query: sql=%s params=%s", sql, params)
    return {"sql": sql, "params": params}


async def narrow(state: AgentState) -> dict[str, Any]:
    """Run the query and collect candidates.

    Without vectors there is no row cap, so ``rows`` is the complete match set and
    its length is the true count. With them the statement carries a ``LIMIT`` and
    ``rows`` is the nearest slice.

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
        _logger.warning("narrow: query failed: %s", exc)
        return {"error": f"Query failed: {exc}"}
    _logger.info("narrow: matched %d rows", len(rows))
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
        _logger.info("answer: replying conversationally (clarifying question)")
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
        _logger.warning("answer: reporting upstream error: %s", failure)
        writer({"answer": f"I could not run that query. {failure}"})
        return {}

    rows = state.get("rows", [])
    filters = state.get("filters")
    semantic = state.get("semantic", {})

    # the model is told how the rows were found, so it does not call a ranked set complete
    retrieval_context = {
        "table": state.get("target", "unknown"),
        "filters_applied": filters.model_dump(exclude_none=True) if filters else {},
        "searched_by_meaning": semantic,
        "ranked_by_similarity": bool(state.get("vectors")),
        "row_count": len(rows),
        "rows": rows,
    }

    _logger.info("answer: summarising %d rows from %s", len(rows), retrieval_context["table"])
    usage: dict[str, int] = {}
    try:
        async for token in stream_chat(
            [
                {
                    "role": "user",
                    "content": (
                        f"Question: {state['question']}\n\n"
                        f"Retrieved:\n{json.dumps(retrieval_context, default=str)}"
                    ),
                }
            ],
            system=ANSWER_SYSTEM,
            usage=usage,
        ):
            writer({"answer": token})
    except openai.APIStatusError as exc:
        _logger.warning("answer: model rejected the result set: %s", exc)
        writer({"answer": _oversized_result_message(len(rows), exc)})
        return {}

    return {"tokens": usage}


def _oversized_result_message(row_count: int, exc: openai.APIStatusError) -> str:
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
    """Decide whether to embed, to query straight away, or to ask a follow-up.

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

    # embedding costs an API call, so it is skipped unless a term was actually found
    if any(state.get("semantic", {}).values()):
        return "embed"
    return "build_query"
