"""Context-window arithmetic for the gauge.

The window is shared between the prompt and the completion, so the room left for
conversation history is the window less the fixed prompt overhead less space for
the model to reply. Reasoning tokens count against the completion and measured
around 75% of it on this model, so the reserve is sized for thinking rather than
for the visible text.

Token counts are ``chars // 4``. Measured against real ``prompt_tokens`` on
``gpt-oss-120b`` that lands within about 5%, and it only ever feeds a display
gauge whose denominator is six figures. tiktoken has no encoding for
``o200k_harmony``, so it would not be more accurate here.

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

Sized for reasoning, not for the answer. The visible reply is short, but the
thinking that precedes it ran four to five times longer in testing and occupies
the same window.
"""

CHARS_PER_TOKEN = 4

_SEED_OVERHEAD = sum(
    len(text)
    for text in (
        AGENT_SYSTEM,
        *(tool.description for tool in (search_orders, search_tonnage, ask_user)),
        *(json.dumps(tool.args) for tool in (search_orders, search_tonnage, ask_user)),
    )
) // CHARS_PER_TOKEN
"""Fixed cost of a model call before any history.

The system prompt plus every tool's description and argument schema, all sent on
every call. Computed at import from the live objects, so editing a prompt or a
tool docstring cannot leave a stale constant behind.
"""

USABLE_TOKENS = CONTEXT_WINDOW - _SEED_OVERHEAD - COMPLETION_RESERVE
"""Space available to conversation history.

Overhead and reserve are already deducted, so a new conversation reads exactly
zero rather than starting part-full.
"""


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
    return sum(len(m.get("content", "")) for m in history) // CHARS_PER_TOKEN
