"""Graph state for the retrieval agent."""

from __future__ import annotations

from typing import Any, Required, TypedDict

from ai_platform.backend.tables import OrderFilters, VesselFilters

TableFilters = OrderFilters | VesselFilters


class AgentState(TypedDict, total=False):
    """State carried between nodes.

    Attributes
    ----------
    question : str
        The user's latest message. Always present at entry.
    history : list of dict
        Prior turns in OpenAI message format, oldest first.
    target : str
        Table the question resolved to.
    filters : TableFilters
        Structured filter for that table.
    semantic : dict of str to str
        Free-text terms per embeddable column, as extracted.
    vectors : list of tuple
        ``(field, embedding)`` pairs from ``embed``, ordering the query by
        similarity.
    sql : str
        The statement built from the filters, kept for display and debugging.
    params : list
        Bound parameters. Never interpolated into ``sql``.
    rows : list of dict
        Candidates returned by ``narrow``.
    clarifying_question : str
        Set when the question carries nothing filterable.
    error : str
        Set when a node fails, so ``answer`` can report it rather than crash.
    summary : str
        Written by ``compact``. The earlier conversation, condensed.
    tokens : dict of str to int
        Usage reported by whichever node last called the model. Nodes overwrite
        rather than accumulate; the caller sums across stream updates, which
        avoids a reducer for a number nothing downstream reads.
    """

    # set at entry
    question: Required[str]
    history: list[dict[str, str]]

    # written by extract_filters and embed
    target: str
    filters: TableFilters
    semantic: dict[str, str]
    vectors: list[tuple[str, list[float]]]

    # written by build_query and narrow
    sql: str
    params: list[Any]
    rows: list[dict[str, Any]]

    # any one of these sends the run straight to answer
    clarifying_question: str
    error: str
    summary: str
    tokens: dict[str, int]
