"""Build and compile the retrieval agent.

Stage one only: extract a filter, compile it to SQL, run it, answer from what
came back. Ranking, fusion and reranking follow once the gold layer exists, and
slot between ``narrow`` and ``answer`` without disturbing the rest.

The entry point is conditional so history can be compacted before anything reads
it. Compaction costs a model call, so the router skips it until history is large
enough to be worth one.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ai_platform.backend.nodes import (
    answer,
    build_query,
    compact,
    extract_filters,
    narrow,
    route_after_extract,
    route_entry,
)
from ai_platform.backend.state import AgentState

_builder = StateGraph(AgentState)

_builder.add_node("compact", compact)
_builder.add_node("extract_filters", extract_filters)
_builder.add_node("build_query", build_query)
_builder.add_node("narrow", narrow)
_builder.add_node("answer", answer)

_builder.add_conditional_edges(START, route_entry, ["compact", "extract_filters"])
_builder.add_edge("compact", "extract_filters")
_builder.add_conditional_edges(
    "extract_filters", route_after_extract, ["build_query", "answer"]
)
_builder.add_edge("build_query", "narrow")
_builder.add_edge("narrow", "answer")
_builder.add_edge("answer", END)

graph = _builder.compile()
