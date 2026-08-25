"""Model access for the plain-model chat profile.

Imports no chainlit. That is what lets this run in the eval suite with no web
server, and what keeps the reply path usable outside the chat interface.

Groq is reached through the OpenAI-compatible endpoint rather than the ``groq``
SDK, because ``cl.instrument_openai()`` only instruments the OpenAI client — and,
per the same shape, because ``langfuse.openai`` is a drop-in ``AsyncOpenAI`` that
sends every completion call to Langfuse as a trace/generation with no per-call
instrumentation code.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import cast

from dotenv import load_dotenv
from langfuse.openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

load_dotenv()

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "You are the assistant for Ocean Intelligence, a platform covering shipping "
    "fleet and cargo data: vessels, tonnage, open positions, laycans, load and "
    "discharge ports, and cargo orders.\n"
    "\n"
    "You currently have no access to the database or to any retrieval system. "
    "Answer from general maritime and shipping knowledge only. When a question "
    "needs specific records — a particular vessel, a live position, counts, "
    "anything that would require looking data up — say plainly that you cannot "
    "see the data yet, rather than inventing a plausible answer. Never present a "
    "guess as a lookup."
)


def get_model_name() -> str:
    """Read the model to call.

    Returns
    -------
    str
        Model identifier, defaulting to ``openai/gpt-oss-120b``.
    """
    return os.environ.get("GROQ_MODEL", "").strip() or DEFAULT_MODEL


def get_client() -> AsyncOpenAI:
    """Build the Groq client.

    Returns
    -------
    AsyncOpenAI
        Client pointed at Groq's OpenAI-compatible endpoint.

    Raises
    ------
    RuntimeError
        If ``GROQ_API_KEY`` is unset.
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Check .env.")

    return AsyncOpenAI(
        api_key=api_key,
        base_url=os.environ.get("GROQ_BASE_URL", "").strip() or DEFAULT_BASE_URL,
    )


async def stream_chat(
    messages: list[dict[str, str]],
    system: str = SYSTEM_PROMPT,
    usage: dict[str, int] | None = None,
) -> AsyncIterator[str]:
    """Stream a reply for a conversation.

    The system prompt is a parameter rather than a constant because the agent's
    answer step needs its own framing. Defaulting it here keeps every other
    entry point on the plain-model prompt without repeating it.

    Token counts cannot be returned from a generator, so a caller wanting them
    passes a dict to be filled in. They arrive on the final chunk, which carries
    no ``choices`` — hence the usage check sits ahead of the skip, not after it.

    Parameters
    ----------
    messages : list of dict
        Conversation so far in OpenAI format, oldest first.
    system : str
        System prompt prepended to the payload.
    usage : dict of str to int, optional
        Filled with ``prompt_tokens``, ``completion_tokens`` and
        ``reasoning_tokens`` once the stream closes. Left untouched if omitted.

    Yields
    ------
    str
        Content deltas in arrival order. Reasoning deltas emitted by ``gpt-oss``
        models are skipped — only ``delta.content`` is forwarded. They are still
        billed and still occupy the window, which is what ``reasoning_tokens``
        reports.

    Raises
    ------
    RuntimeError
        If ``GROQ_API_KEY`` is unset.
    """
    client = get_client()
    payload = cast(
        "list[ChatCompletionMessageParam]",
        [{"role": "system", "content": system}, *messages],
    )
    stream = await client.chat.completions.create(
        model=get_model_name(),
        messages=payload,
        stream=True,
        stream_options={"include_usage": True},
    )

    async for chunk in stream:
        if chunk.usage is not None and usage is not None:
            details = chunk.usage.completion_tokens_details
            usage["prompt_tokens"] = chunk.usage.prompt_tokens
            usage["completion_tokens"] = chunk.usage.completion_tokens
            usage["reasoning_tokens"] = (details.reasoning_tokens or 0) if details else 0
        if not chunk.choices:
            continue
        content = chunk.choices[0].delta.content
        if content:
            yield content
