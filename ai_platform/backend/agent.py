"""Compile the chartering agent.

One agent, three tools, three middleware. Everything the old ``StateGraph`` did by
routing between nodes is now either a tool call the model chooses or a middleware
hook on the loop.

A checkpointer is required, not optional: ``ask_user`` calls ``interrupt``, which
saves the run and stops, and there has to be somewhere to save it. ``InMemorySaver``
loses pending interrupts on restart, which is fine while proving this and not fine
in front of a desk.
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
    ToolErrorMiddleware,
)
from langgraph.checkpoint.memory import InMemorySaver

from ai_platform.backend.llm import get_chat_model
from ai_platform.backend.logging_utils import get_logger
from ai_platform.backend.prompts import AGENT_SYSTEM
from ai_platform.backend.tools import ask_user, search_orders, search_tonnage

_logger = get_logger("agent")

RUN_LIMIT = 6
"""Model calls allowed per question.

Per question rather than per conversation — a thread limit would stop the agent
answering after a few turns. A simple search takes two calls, matching takes
three, a failed search plus clarification takes four, and both together take five,
so six leaves one spare and catches a loop on its first extra pass.
"""

SUMMARISE_AT = 0.8
"""Fraction of the context window at which history is condensed."""

KEEP_RECENT_MESSAGES = 6
"""Messages left verbatim when summarising. Three exchanges."""


def _describe_tool_failure(error: Exception, request: object) -> str:
    """Turn a failed search into something the model can act on.

    Groq reports a context overflow as "reduce the length of the messages" with
    no numbers in it, so the row count is the only figure worth surfacing — and
    the model can respond by narrowing rather than giving up.

    Parameters
    ----------
    error : Exception
        Whatever the tool raised.
    request : object
        The tool call that failed.

    Returns
    -------
    str
        Message handed back to the model in place of the tool result.
    """
    _logger.warning("tool failed: %s", error)
    return (
        f"That search failed: {error}. Narrow it with a tighter date range, a "
        "size bound or an id, then try again."
    )


model = get_chat_model()

# ModelCallLimitMiddleware widens the agent state, and the middleware
# parameter is invariant in it, so the mixed list needs the annotation
_middleware: list[AgentMiddleware[Any, Any, Any]] = [
    SummarizationMiddleware(
        model=model,
        trigger=("fraction", SUMMARISE_AT),
        keep=("messages", KEEP_RECENT_MESSAGES),
    ),
    ToolErrorMiddleware(on_error=_describe_tool_failure),
    ModelCallLimitMiddleware(run_limit=RUN_LIMIT, exit_behavior="end"),
]

agent = create_agent(
    model=model,
    tools=[search_orders, search_tonnage, ask_user],
    system_prompt=AGENT_SYSTEM,
    middleware=_middleware,
    checkpointer=InMemorySaver(),
)

_logger.info("agent compiled with 3 tools")
