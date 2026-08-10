"""Chainlit entry point.

The only module in this project that imports chainlit. Model and agent
behaviour belongs in ``ai_platform.backend`` so it stays runnable without a web
server.
"""

from __future__ import annotations

import chainlit as cl
from chainlit.types import ThreadDict

from ai_platform.app.data_layer import get_data_layer
from ai_platform.backend.llm import stream_chat

__all__ = ["get_data_layer"]

DEV_USERNAME = "dev"
DEV_PASSWORD = "dev"

PLAIN_MODEL_PROFILE = "plain-model"

cl.instrument_openai()


def root_message(content: str = "") -> cl.Message:
    """Build a message that is not nested under the current run step.

    Chainlit wraps ``on_chat_start`` and ``on_message`` in steps named in
    ``CL_RUN_NAMES``, and ``Message.__post_init__`` adopts the innermost active
    step as ``parent_id``. Those run steps are never written to the data layer,
    so the message persists pointing at a parent that does not exist and the UI
    drops it when replaying a thread — saved, but invisible on resume.

    Parameters
    ----------
    content : str
        Initial message content.

    Returns
    -------
    cl.Message
        Message pinned to the top level of the thread.
    """
    message = cl.Message(content=content)
    message.parent_id = None
    return message


@cl.set_chat_profiles
async def list_chat_profiles(
    current_user: cl.User | None,
    thread_id: str | None,
) -> list[cl.ChatProfile]:
    """Offer the modes a user can start a conversation in.

    Profiles are chosen at thread start and cannot be switched mid-conversation,
    so each thread is permanently tagged with the mode that produced it. The
    LangGraph agent becomes a second entry here rather than a rewrite of
    ``handle_message``.

    Parameters
    ----------
    current_user : cl.User or None
        Authenticated user, available for restricting profiles per role later.
    thread_id : str or None
        Thread being opened, when resuming an existing one.

    Returns
    -------
    list of cl.ChatProfile
        Profiles offered in the picker.
    """
    return [
        cl.ChatProfile(
            name=PLAIN_MODEL_PROFILE,
            display_name="Plain model",
            markdown_description=(
                "Answers from general shipping knowledge. **No database or "
                "document access.**"
            ),
            default=True,
        )
    ]


@cl.password_auth_callback
async def authenticate(username: str, password: str) -> cl.User | None:
    """Authenticate a local developer against hardcoded credentials.

    Placeholder for Phase 2, which replaces this with header auth against the
    identity provider. The returned ``identifier`` is what threads are keyed on,
    so it must stay stable across restarts or existing history is orphaned.

    Parameters
    ----------
    username : str
        Submitted username.
    password : str
        Submitted password.

    Returns
    -------
    cl.User or None
        The authenticated user, or None to reject the login.
    """
    if username == DEV_USERNAME and password == DEV_PASSWORD:
        return cl.User(identifier=DEV_USERNAME, metadata={"role": "dev"})
    return None


@cl.on_chat_start
async def start_chat() -> None:
    """Greet the user at the top of a new conversation.

    Returns
    -------
    None
    """
    await root_message(
        "Ready. I can answer general shipping questions, but I have no "
        "database or document access yet, so I will say so rather than "
        "guess at specific records."
    ).send()


@cl.on_chat_resume
async def resume_chat(thread: ThreadDict) -> None:
    """Reattach to a persisted conversation.

    Without this callback old threads are listed and readable but cannot be
    continued. Chainlit replays the stored steps itself, so there is no state to
    rebuild until the agent carries memory.

    Parameters
    ----------
    thread : ThreadDict
        Stored thread record supplied by the data layer. Unused, but the
        callback must exist for resume to be offered at all.

    Returns
    -------
    None
    """


@cl.on_message
async def handle_message(message: cl.Message) -> None:
    """Answer an inbound message with the profile's reply path.

    Branches on the profile even though only one exists, so adding the agent is
    another branch rather than a rewrite. History comes from
    ``cl.chat_context``, which already holds the thread in OpenAI format.

    ``send()`` is what ends the stream, so it must come after the tokens, not
    before. Sending first and calling ``update()`` at the end persists the text
    but never renders it — the UI is left waiting for a stream that never closes.

    Parameters
    ----------
    message : cl.Message
        Inbound message from the user. Already appended to ``chat_context``, so
        it is not passed separately.

    Returns
    -------
    None
    """
    profile = cl.user_session.get("chat_profile") or PLAIN_MODEL_PROFILE
    if profile != PLAIN_MODEL_PROFILE:
        await root_message(f"Profile {profile!r} is not wired up yet.").send()
        return

    reply = root_message()
    async for token in stream_chat(cl.chat_context.to_openai()):
        await reply.stream_token(token)
    await reply.send()
