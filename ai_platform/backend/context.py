"""Context-window arithmetic for the gauge.

The window is shared between the prompt and the completion, so the room left for
conversation history is the window less the fixed prompt overhead less space for
the model to reply. Reasoning tokens count against the completion and measured
around 75% of it on this model, so the reserve is sized for thinking rather than
for the visible text.

Imports nothing from chainlit, so the gauge is testable without a web server.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import tiktoken
from langchain_core.messages import BaseMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from ai_platform.backend.prompts import AGENT_SYSTEM
from ai_platform.backend.tools import ask_user, search_orders_and_tonnage

CONTEXT_WINDOW = 131_072
"""Published limit for ``openai/gpt-oss-120b``, shared by prompt and completion."""

COMPLETION_RESERVE = 2_000
"""Space held back so the model can always reply.

Sized for reasoning, not for the answer. The visible reply is short, but the
thinking that precedes it ran four to five times longer in testing and occupies
the same window.
"""

_ENCODING = tiktoken.get_encoding("o200k_harmony")
"""The tokeniser ``gpt-oss-120b`` actually uses."""

MESSAGE_OVERHEAD = 4
"""Tokens the harmony wrapper costs per message on top of its content.

``<|start|>role<|message|>`` and ``<|end|>`` around every turn.
"""


def count_tokens(text: str) -> int:
    """Count tokens in a string.

    Special tokens are encoded as ordinary text: user content is untrusted and
    a literal ``<|endoftext|>`` in it must not raise.

    Parameters
    ----------
    text : str
        Text to measure.

    Returns
    -------
    int
        Token count.
    """
    return len(_ENCODING.encode(text, disallowed_special=()))


_SEED_OVERHEAD = count_tokens(AGENT_SYSTEM) + sum(
    count_tokens(json.dumps(convert_to_openai_tool(tool)))
    for tool in (search_orders_and_tonnage, ask_user)
)
"""Fixed cost of a model call before any history.

The system prompt plus every tool as it goes on the wire, all sent on every call.
Computed at import from the live objects, so editing a prompt or a tool docstring
cannot leave a stale constant behind.

Measured on the converted tool rather than ``tool.args``, which leaves ``$ref``
unresolved and so omits every field of the nested search models — 87 tokens
against a true 2,996 for the search tool. The conversion already carries the
docstring as ``description``, so that must not be added again.
"""

USABLE_TOKENS = CONTEXT_WINDOW - _SEED_OVERHEAD - COMPLETION_RESERVE
"""Space available to conversation history.

Overhead and reserve are already deducted, so a new conversation reads exactly
zero rather than starting part-full.
"""


def content_text(content: Any) -> str:
    """Flatten a message's content to the text the model is sent.

    Parameters
    ----------
    content : Any
        A string, or a list of content parts as some providers return.

    Returns
    -------
    str
        The text, with list parts joined; non-text parts are serialised.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return "" if content is None else str(content)


def message_tokens(messages: Sequence[BaseMessage]) -> int:
    """Measure the token cost of the agent's stored conversation.

    Counts what goes on the wire for each message: its content, which for a tool
    message is the full result with every row, plus the name and arguments of
    every tool call an assistant message made.

    Parameters
    ----------
    messages : Sequence of BaseMessage
        The agent's message history from the checkpointer.

    Returns
    -------
    int
        Tokens across every message plus its wrapper.
    """
    total = 0
    for message in messages:
        total += count_tokens(content_text(message.content)) + MESSAGE_OVERHEAD
        for call in getattr(message, "tool_calls", None) or []:
            total += count_tokens(call.get("name", ""))
            total += count_tokens(json.dumps(call.get("args", {}), default=str))
    return total
