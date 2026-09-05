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

import tiktoken
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


def history_tokens(history: list[dict[str, str]]) -> int:
    """Measure the token cost of a conversation.

    Parameters
    ----------
    history : list of dict
        Prior turns in OpenAI message format.

    Returns
    -------
    int
        Tokens across every message's content plus its wrapper.
    """
    return sum(
        count_tokens(message.get("content", "")) + MESSAGE_OVERHEAD
        for message in history
    )
