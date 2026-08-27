"""Build and compile the retrieval agent.

Extract a filter, embed any free-text terms, compile both to SQL, run it, answer
from what came back. ``embed`` is skipped when the question names nothing that
has to be matched by meaning, since it costs an API call.

The entry point is conditional so history can be compacted before anything reads
it. Compaction costs a model call, so the router skips it until history is large
enough to be worth one.

The compiled graph carries a Langfuse callback handler bound via ``with_config``,
so every run — one node per span, nested under one trace — is logged regardless
of who calls ``.astream``/``.ainvoke``. The node-level completions inside
``llm.py`` link back into that same trace via ``trace_kwargs`` there, rather
than each opening its own.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ai_platform.backend.logging_utils import get_logger
from ai_platform.backend.nodes import (
    answer,
    build_query,
    compact,
    embed,
    extract_filters,
    narrow,
    route_after_extract,
    route_entry,
)
from ai_platform.backend.state import AgentState
from ai_platform.backend.tracing import langfuse_handler

_logger = get_logger("agent")

_builder = StateGraph(AgentState)

_builder.add_node("compact", compact)
_builder.add_node("extract_filters", extract_filters)
_builder.add_node("embed", embed)
_builder.add_node("build_query", build_query)
_builder.add_node("narrow", narrow)
_builder.add_node("answer", answer)

_builder.add_conditional_edges(START, route_entry, ["compact", "extract_filters"])
_builder.add_edge("compact", "extract_filters")
_builder.add_conditional_edges(
    "extract_filters", route_after_extract, ["embed", "build_query", "answer"]
)
_builder.add_edge("embed", "build_query")
_builder.add_edge("build_query", "narrow")
_builder.add_edge("narrow", "answer")
_builder.add_edge("answer", END)

# bound here, not at each call site, so every caller (Chainlit UI, headless eval)
# gets a Langfuse trace with no per-invocation wiring
graph = _builder.compile().with_config({"callbacks": [langfuse_handler]})

_logger.info("agent graph compiled")
