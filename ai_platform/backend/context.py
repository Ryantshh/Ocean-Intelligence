"""Context-window arithmetic for the retrieval agent.

The window is shared between the prompt and the completion, so the room left for
conversation history is the window less the fixed prompt overhead less space for
the model to reply. Reasoning tokens count against the completion and measured
around 75% of it on this model, so the reserve is sized for thinking rather than
for the visible text.

Token counts are ``chars // 4``. Measured against real ``prompt_tokens`` on
``gpt-oss-120b`` that lands within about 5%, and it only ever feeds a display
gauge whose denominator is six figures. tiktoken is not used: it is not
installed, and it has no encoding for ``o200k_harmony``.

Imports nothing from chainlit, so the gauge is testable without a web server.
"""

from __future__ import annotations

import json

from ai_platform.backend.prompts import AGENT_SYSTEM
from ai_platform.backend.tools import ask_user, search_orders, search_tonnage

CONTEXT_WINDOW = 131_072
"""Published limit for ``openai/gpt-oss-120b``, shared by prompt and completion."""

COMPLETION_RESERVE = 2_000
"""Space held back so the model can always reply.

Sized for reasoning, not for the answer. Extraction emits a small JSON object
bounded by the schema, but the thinking that precedes it ran four to five times
the visible output in testing and occupies the same window.
"""

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate the token cost of a string.

    Parameters
    ----------
    text : str
        Text to measure.

    Returns
    -------
    int
        Estimated tokens.
    """
    return len(text) // CHARS_PER_TOKEN


def history_tokens(history: list[dict[str, str]]) -> int:
    """Approximate the token cost of a conversation.

    Parameters
    ----------
    history : list of dict
        Prior turns in OpenAI message format.

    Returns
    -------
    int
        Estimated tokens across every message's content.
    """
    return sum(estimate_tokens(message.get("content", "")) for message in history)


SEED_OVERHEAD = estimate_tokens(AGENT_SYSTEM) + sum(
    estimate_tokens(json.dumps(tool.args_schema.model_json_schema()))
    + estimate_tokens(tool.description)
    for tool in (search_orders, search_tonnage, ask_user)
)
"""Fixed cost of a model call before any history.

The system prompt plus every tool's description and argument schema, all of which
are sent on every call. Recomputed at import from the live objects, so editing a
prompt or a tool docstring cannot leave a stale constant behind. Replaced by a
real measurement once one arrives.
"""

def usable_tokens() -> int:
    """Report the space available to conversation history.

    Returns
    -------
    int
        Window less fixed overhead less the completion reserve.
    """
    return CONTEXT_WINDOW - SEED_OVERHEAD - COMPLETION_RESERVE


def fill_fraction(history: list[dict[str, str]]) -> float:
    """Report how full the usable context is, from 0 to 1.

    Reads exactly zero on a new conversation: overhead and reserve are already
    deducted from the denominator, so the numerator is history alone.

    Parameters
    ----------
    history : list of dict
        Prior turns in OpenAI message format.

    Returns
    -------
    float
        Fraction of usable space consumed, clamped to 1.0.
    """
    usable = usable_tokens()
    if usable <= 0:
        return 1.0
    return min(history_tokens(history) / usable, 1.0)


