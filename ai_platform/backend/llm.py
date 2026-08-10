"""Model access for the plain-model chat profile.

Imports no chainlit. That is what lets this run in the eval suite with no web
server, and what keeps the reply path usable outside the chat interface.

Groq is reached through the OpenAI-compatible endpoint rather than the ``groq``
SDK, because ``cl.instrument_openai()`` only instruments the OpenAI client.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import cast

from dotenv import load_dotenv
from openai import AsyncOpenAI
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


async def stream_chat(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """Stream a reply for a conversation.

    The system prompt is prepended here rather than by the caller, so every
    entry point gets the same framing.

    Parameters
    ----------
    messages : list of dict
        Conversation so far in OpenAI format, oldest first.

    Yields
    ------
    str
        Content deltas in arrival order. Reasoning deltas emitted by ``gpt-oss``
        models are skipped — only ``delta.content`` is forwarded.

    Raises
    ------
    RuntimeError
        If ``GROQ_API_KEY`` is unset.
    """
    client = get_client()
    payload = cast(
        "list[ChatCompletionMessageParam]",
        [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
    )
    stream = await client.chat.completions.create(
        model=get_model_name(),
        messages=payload,
        stream=True,
    )

    async for chunk in stream:
        if not chunk.choices:
            continue
        content = chunk.choices[0].delta.content
        if content:
            yield content
