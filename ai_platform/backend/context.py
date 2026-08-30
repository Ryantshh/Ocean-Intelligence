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

COMPACT_AT = 0.80
"""Fraction of usable space that triggers compaction.

Policy, not capacity: deliberately below the point where a call would fail, so
compaction runs while there is still room to run it in.
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

_measured_overhead: int | None = None


def record_prompt_tokens(prompt_tokens: int, history: list[dict[str, str]]) -> None:
    """Refine the fixed overhead from a real extraction call.

    Keeps the smallest value seen rather than the first. A fresh conversation
    starts with no history and gives a clean reading, but a resumed thread
    carries history into its first call and would read high; the leanest prompt
    observed is the closest thing to pure overhead. The reading still includes
    the question itself, a couple of dozen tokens, which errs towards a smaller
    usable budget and so towards compacting slightly early.

    Parameters
    ----------
    prompt_tokens : int
        ``usage.prompt_tokens`` from the extraction response.
    history : list of dict
        History sent on that same call.

    Returns
    -------
    None
    """
    global _measured_overhead
    candidate = prompt_tokens - history_tokens(history)
    if candidate > 0 and (_measured_overhead is None or candidate < _measured_overhead):
        _measured_overhead = candidate


def fixed_overhead() -> int:
    """Report the fixed cost of an extraction call.

    Returns
    -------
    int
        The measured overhead when one has been recorded, else the seed.
    """
    return SEED_OVERHEAD if _measured_overhead is None else _measured_overhead


def usable_tokens() -> int:
    """Report the space available to conversation history.

    Returns
    -------
    int
        Window less fixed overhead less the completion reserve.
    """
    return CONTEXT_WINDOW - fixed_overhead() - COMPLETION_RESERVE


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


def should_compact(history: list[dict[str, str]]) -> bool:
    """Decide whether history has grown enough to summarise.

    Parameters
    ----------
    history : list of dict
        Prior turns in OpenAI message format.

    Returns
    -------
    bool
        True once the fill fraction reaches :data:`COMPACT_AT`.
    """
    return fill_fraction(history) >= COMPACT_AT
