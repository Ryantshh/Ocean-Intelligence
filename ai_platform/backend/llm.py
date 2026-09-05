"""Model access for the plain-model chat profile.

Imports no chainlit. That is what lets this run in the eval suite with no web
server, and what keeps the reply path usable outside the chat interface.

Groq is reached through the OpenAI-compatible endpoint rather than the ``groq``
SDK, so any OpenAI-client instrumentation applies to it unchanged.

Nothing here may monkeypatch ``AsyncCompletions.create`` process-wide. Once a
streaming callback is attached, ``langchain_openai`` awaits ``create`` and then
uses the result as an async context manager; an instrumenter that returns an
async generator instead of the ``AsyncStream`` breaks every agent turn with a
TypeError. That is why ``cl.instrument_openai()`` is not called anywhere.
Langfuse reaches the agent through the ``CallbackHandler`` on its run config,
which is the supported LangChain path and patches nothing.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import SecretStr

load_dotenv()

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"

MAX_INPUT_TOKENS = 131_072
"""Context window of the default model.

Declared here because LangChain carries profiles for known providers only,
and a custom base URL is not one. Without it any middleware working in
fractions of the window raises rather than guessing."""

MAX_OUTPUT_TOKENS = 2048
"""Cap on one completion, reasoning included.

A reply is a sentence and five bullets; the reasoning before it measured four to
five times that. The cap ends a degenerating generation — ``gpt-oss`` on Groq
has emitted thousands of zero-width spaces in one turn — before it runs to the
model's own limit.
"""

SYSTEM_PROMPT = Path(__file__).with_name("plain_model.md").read_text(encoding="utf-8")


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


def get_chat_model() -> ChatOpenAI:
    """Build the LangChain chat model the agent runs on.

    The same Groq endpoint ``get_client`` uses, wrapped in the LangChain
    interface ``create_agent`` requires. Two clients rather than one because the
    plain-model chat profile streams through the OpenAI SDK directly.

    Returns
    -------
    ChatOpenAI
        Pointed at Groq's OpenAI-compatible endpoint.

    Raises
    ------
    RuntimeError
        If ``GROQ_API_KEY`` is unset.
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Check .env.")

    return ChatOpenAI(
        model=get_model_name(),
        api_key=SecretStr(api_key),
        base_url=os.environ.get("GROQ_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        temperature=0,
        max_completion_tokens=MAX_OUTPUT_TOKENS,
        profile={"max_input_tokens": MAX_INPUT_TOKENS},
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
