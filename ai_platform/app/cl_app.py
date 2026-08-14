"""Chainlit entry point.

The only module in this project that imports chainlit. Model and agent
behaviour belongs in ``ai_platform.backend`` so it stays runnable without a web
server.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, cast

import chainlit as cl
from chainlit.types import ThreadDict

from ai_platform.app.data_layer import get_data_layer
from ai_platform.backend.context import (
    fill_fraction,
    history_tokens,
    should_compact,
    usable_tokens,
)
from ai_platform.backend.graph import graph
from ai_platform.backend.llm import stream_chat
from ai_platform.backend.tables import resolve

__all__ = ["get_data_layer"]

DEV_USERNAME = "dev"
DEV_PASSWORD = "dev"

PLAIN_MODEL_PROFILE = "plain-model"
AGENT_PROFILE = "agent"

GAUGE_SESSION_KEY = "context_gauge"
COMPACTION_SESSION_KEY = "compaction"

RESULTS_ELEMENT = "Results"
"""Element name for the results table.

Load-bearing three times over. It selects ``public/elements/Results.jsx``, it
titles the side panel, and Chainlit turns any occurrence of it in the reply text
into the link that reopens the panel. It therefore cannot carry the row count,
which lives in the reply prose and in the table's own footer instead.
"""

STEP_READING = "Reading your question"
STEP_RETRIEVING = "Retrieving data"
STEP_COMPACTING = "Compacting conversation"

AGENT_DESCRIPTION = """Your supply and demand AI Agent for vessel positions and cargo enquiries.

For vessels, you can narrow by vessel id, availability dates, ETA, deadweight
tonnage, ballast or laden, and commercial status.

For cargoes, you can narrow by order id, laycan window, date received, last
updated, and cargo weight.

Just ask in plain language and I will take it from there."""

AGENT_STARTER_PROMPTS = (
    "Vessels open in December 2025 in ballast",
    "Vessels of at least 180,000 dwt open in January 2026",
    "Cargoes with laycan in November 2025 over 150,000 tonnes",
)

AGENT_STARTERS = [
    cl.Starter(label=prompt, message=prompt) for prompt in AGENT_STARTER_PROMPTS
]

cl.instrument_openai()


def agent_history() -> list[dict[str, str]]:
    """Assemble the conversation the graph should see.

    Chainlit's data layer is the durable record of a thread, so a compaction
    summary is derived data rather than truth and lives only in the session.
    When one exists it stands in for the messages it replaced and later turns
    are appended raw; when it does not — a restart, or a resumed thread — the
    full transcript is used and compaction simply runs again.

    Returns
    -------
    list of dict
        Prior turns in OpenAI format, excluding the question being asked.
    """
    raw = cl.chat_context.to_openai()[:-1]
    stored = cl.user_session.get(COMPACTION_SESSION_KEY)
    if stored and len(raw) >= stored["raw_count"]:
        return [*stored["history"], *raw[stored["raw_count"] :]]
    return raw


def json_safe(value: Any) -> Any:
    """Convert a driver value into something ``json.dumps`` accepts.

    ``CustomElement.__post_init__`` serialises props with a bare ``json.dumps``
    and no ``default``, so an unconverted date raises before the element is ever
    sent. Dates become ISO strings, which also sort chronologically as strings
    and so need no special handling in the table.

    Decimals become native numbers. Left alone they serialise as strings and the
    table sorts them lexicographically — 90,000 tonnes would rank above 187,000.
    Integral values become ``int`` rather than ``float`` because ``order_id`` on
    tonnage is numeric and runs to eighteen digits, which a float corrupts.

    Parameters
    ----------
    value : Any
        Cell value straight from the driver.

    Returns
    -------
    Any
        A JSON-serialisable equivalent, or the value untouched.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time.min else value.isoformat(" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def results_props(target: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the props for the results table element.

    Column choice, null substitution and the word for a row all come from the
    table module, so the UI carries no knowledge of what the columns mean.

    Rows go as arrays of values rather than dicts. Aligned to ``columns`` they
    drop the repeated key on every field, which matters at several thousand rows.

    Parameters
    ----------
    target : str
        Table the question resolved to.
    rows : list of dict
        Every matching row.

    Returns
    -------
    dict
        ``columns``, ``rows`` and ``noun``, ready to hand to the element.
    """
    module = resolve(target)
    columns = list(module.DISPLAY_COLUMNS)
    defaults = module.DISPLAY_DEFAULTS
    return {
        "columns": columns,
        "rows": [
            [
                json_safe(
                    row.get(column)
                    if row.get(column) is not None
                    else defaults.get(column)
                )
                for column in columns
            ]
            for row in rows
        ],
        "noun": module.DISPLAY_NOUN,
    }


async def refresh_gauge(
    history: list[dict[str, str]], spent: int = 0, anchor: str = ""
) -> None:
    """Create or update the context gauge pinned in the corner.

    An element only renders inside the message whose id equals its ``for_id``;
    the frontend rejects an empty one outright, so the gauge has to be anchored
    to a real message even though it is drawn fixed to the viewport. The anchor
    is therefore the first reply of the session — nothing is sent before that,
    because a message on chat start would replace the starter buttons. Once set
    it is reused, which is what keeps a single gauge rather than one per reply
    stacked on top of each other.

    Sent unpersisted because it reports live state with no historical meaning.
    Saved, it would replay on resume as a row of stale gauges.

    Parameters
    ----------
    history : list of dict
        Conversation the next question will carry.
    spent : int
        Tokens consumed answering the question just finished.
    anchor : str
        Id of the message to attach to, used only on the first call.

    Returns
    -------
    None
    """
    props = {
        "percent": round(fill_fraction(history) * 100, 2),
        "used": history_tokens(history),
        "usable": usable_tokens(),
        "spent": spent,
    }
    stored = cl.user_session.get(GAUGE_SESSION_KEY)
    if stored is None:
        if not anchor:
            return
        gauge = cl.CustomElement(name="ContextGauge", props=props, for_id=anchor)
        cl.user_session.set(GAUGE_SESSION_KEY, gauge)
    else:
        gauge = stored
        gauge.props = props
        gauge.content = json.dumps(props)
    target = gauge.for_id or anchor
    if not target:
        return
    await gauge.send(for_id=target, persist=False)


async def run_agent(question: str) -> None:
    """Run the retrieval graph and render everything it produces.

    The graph never imports chainlit; it emits tagged payloads on LangGraph's
    custom stream and one update per node. This is the only place the two meet.

    Progress steps wrap segments of that stream rather than the whole run, so
    the stream is pulled explicitly instead of with a single ``async for``.

    The table's name is appended to the reply on a line of its own, and that line
    is load-bearing. Chainlit builds a regex from the names of a message's
    elements and turns every match in the message text into the link that opens
    the side panel. Without an occurrence of the name there is no link, so once a
    user closes the panel it cannot be reopened. It is appended here rather than
    asked of the model, because the match is an exact string and the model would
    not reliably produce it.

    Parameters
    ----------
    question : str
        The user's latest message.

    Returns
    -------
    None
    """
    history = agent_history()
    reply = root_message()
    events = graph.astream(
        {"question": question, "history": history},
        stream_mode=["updates", "custom"],
    ).__aiter__()

    compaction_step: cl.Step | None = None
    tokens_spent = 0

    async def pump(targets: set[str]) -> dict[str, Any]:
        """Consume events until one of the named nodes reports."""
        nonlocal tokens_spent
        async for mode, payload in events:
            if mode == "custom":
                written = cast("dict[str, str]", payload)
                if "answer" in written:
                    await reply.stream_token(written["answer"])
                elif "compact" in written and compaction_step is not None:
                    await compaction_step.stream_token(written["compact"])
                continue
            node, returned = next(
                iter(cast("dict[str, dict[str, Any] | None]", payload).items())
            )
            update = returned or {}
            tokens_spent += sum(update.get("tokens", {}).values())
            if node in targets:
                return update
        return {}

    if should_compact(history):
        async with cl.Step(name=STEP_COMPACTING) as step:
            compaction_step = step
            compaction = await pump({"compact"})
        compaction_step = None
        if "history" in compaction:
            cl.user_session.set(
                COMPACTION_SESSION_KEY,
                {
                    "history": compaction["history"],
                    "raw_count": len(cl.chat_context.to_openai()) - 1,
                },
            )

    async with cl.Step(name=STEP_READING):
        extraction = await pump({"extract_filters"})

    answered_without_query = bool(
        extraction.get("clarifying_question") or extraction.get("error")
    )
    table_name = ""
    if not answered_without_query:
        async with cl.Step(name=STEP_RETRIEVING):
            narrowed = await pump({"narrow"})
        rows = narrowed.get("rows")
        if rows:
            table_name = RESULTS_ELEMENT
            table = cl.CustomElement(
                name=RESULTS_ELEMENT,
                props=results_props(extraction.get("target", ""), rows),
                display="side",
            )
            reply.elements = cast("list[Any]", [table])

    await pump(set())
    if table_name:
        await reply.stream_token(f"\n\n{table_name}")
    await reply.send()
    await refresh_gauge(agent_history(), tokens_spent, anchor=reply.id)


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
            name=AGENT_PROFILE,
            display_name="Ocean Intelligence Agent",
            markdown_description=AGENT_DESCRIPTION,
            starters=AGENT_STARTERS,
            default=True,
        ),
        cl.ChatProfile(
            name=PLAIN_MODEL_PROFILE,
            display_name="Plain model",
            markdown_description=(
                "Answers from general shipping knowledge. **No database or "
                "document access.**"
            ),
        ),
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

    Both paths yield tokens, so the streaming below is shared. History comes
    from ``cl.chat_context``, which already holds the thread in OpenAI format;
    the agent takes it without the final entry because that is the question
    being asked, which the graph receives separately.

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
    if cl.user_session.get("chat_profile") == PLAIN_MODEL_PROFILE:
        reply = root_message()
        async for token in stream_chat(cl.chat_context.to_openai()):
            await reply.stream_token(token)
        await reply.send()
        return

    await run_agent(message.content)
