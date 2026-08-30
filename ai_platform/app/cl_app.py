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
from langgraph.types import Command

from ai_platform.app.data_layer import get_data_layer
from ai_platform.backend.agent import agent
from ai_platform.backend.context import (
    fill_fraction,
    history_tokens,
    usable_tokens,
)
from ai_platform.backend.llm import stream_chat
from ai_platform.backend.tables import resolve_table

__all__ = ["get_data_layer"]

DEV_USERNAME = "dev"
DEV_PASSWORD = "dev"

PLAIN_MODEL_PROFILE = "plain-model"
AGENT_PROFILE = "agent"

GAUGE_SESSION_KEY = "context_gauge"

RESULTS_ELEMENT = "Results"
"""Element name for the results table.

Load-bearing three times over. It selects ``public/elements/Results.jsx``, it
titles the side panel, and Chainlit turns any occurrence of it in the reply text
into the link that reopens the panel. It therefore cannot carry the row count,
which lives in the reply prose and in the table's own footer instead.
"""

TOOL_STEPS = {
    "search_orders": "Searching cargoes",
    "search_tonnage": "Searching vessels",
    "ask_user": "Waiting on you",
}
"""Progress step shown while each tool runs."""

REFINE_ELEMENT = "RefineSearch"
"""Element name for the form ``ask_user`` renders."""

AGENT_DESCRIPTION = """Your supply and demand AI Agent for vessel positions and cargo enquiries.

For vessels, you can narrow by vessel id, availability dates, ETA, deadweight
tonnage, ballast or laden, and commercial status.

For cargoes, you can narrow by order id, laycan window, date received, last
updated, and cargo weight.

Just ask in plain language and I will take it from there."""

AGENT_STARTER_PROMPTS = (
    "Show me today's cargo list in West Aussie",
    "Show me the latest C3 iron ore orders",
    "What orders are loading in ECSA?",
    "Show me orders from PDM to North China",
    "Show me today's tonnage list open in the Pacific",
    "Show me the ECSA ballasters",
    "Show me the WAF openers",
    "Vessels of at least 180,000 dwt open in January 2026",
    "Cargoes with laycan in November 2025 over 150,000 tonnes",
)

AGENT_STARTERS = [
    cl.Starter(label=prompt, message=prompt) for prompt in AGENT_STARTER_PROMPTS
]

cl.instrument_openai()


def agent_history() -> list[dict[str, str]]:
    """Read the conversation so far, for the context gauge.

    Only the gauge needs this now. The agent keeps its own message history in the
    checkpointer and summarises it through middleware, so nothing here feeds it.

    Returns
    -------
    list of dict
        Prior turns in OpenAI format, excluding the question being asked.
    """
    return cl.chat_context.to_openai()[:-1]


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
    spec = resolve_table(target)
    columns = list(spec.display_columns)
    defaults = spec.display_defaults
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
        "noun": spec.display_noun,
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
    """Run the agent and render everything it produces.

    The agent decides which tools to call and how many times, so there is no
    fixed sequence of steps to wrap. Tool calls are shown as they are chosen and
    closed when their result arrives.

    ``ask_user`` interrupts the run rather than returning, which ends the stream.
    The outer loop exists for that: it shows the form, waits, and resumes with
    what was submitted. It runs more than twice only when the agent asks twice,
    and ``RUN_LIMIT`` bounds it.

    The table's name is appended to the reply on a line of its own, and that line
    is load-bearing. Chainlit builds a regex from the names of a message's
    elements and turns every match in the message text into the link that opens
    the side panel. Without an occurrence of the name there is no link, so once a
    user closes the panel it cannot be reopened.

    Parameters
    ----------
    question : str
        The user's latest message.

    Returns
    -------
    None
    """
    config = {"configurable": {"thread_id": cl.context.session.id}}
    payload: Any = {"messages": [{"role": "user", "content": question}]}
    reply = root_message()
    results_element_name = ""

    while True:
        interrupt_value: dict[str, Any] | None = None
        open_steps: dict[str, cl.Step] = {}

        async for mode, event in agent.astream(
            payload, config, stream_mode=["updates", "messages"]
        ):
            # token-by-token reply text, but only from the agent's own turn
            if mode == "messages":
                chunk, meta = cast("tuple[Any, dict[str, Any]]", event)
                if meta.get("langgraph_node") == "model" and chunk.content:
                    await reply.stream_token(chunk.content)
                continue

            update = cast("dict[str, Any]", event)
            if "__interrupt__" in update:
                interrupt_value = update["__interrupt__"][0].value
                break

            for node, node_update in update.items():
                for message in (node_update or {}).get("messages", []) or []:
                    name = await _render_node_message(
                        message, node, open_steps, reply
                    )
                    results_element_name = name or results_element_name

        for step in open_steps.values():
            await step.__aexit__(None, None, None)

        if interrupt_value is None:
            break

        submitted = await _ask_to_refine(interrupt_value)
        if submitted is None:
            await reply.stream_token(
                "\n\nNo problem — ask again whenever you have those details."
            )
            break
        payload = Command(resume=submitted)

    if results_element_name:
        await reply.stream_token(f"\n\n{results_element_name}")
    await reply.send()


async def _render_node_message(
    message: Any, node: str, open_steps: dict[str, cl.Step], reply: cl.Message
) -> str:
    """Show one message from the agent as a progress step or a results panel.

    Parameters
    ----------
    message : Any
        A LangChain message emitted by a node.
    node : str
        Which node produced it.
    open_steps : dict of str to cl.Step
        Steps opened for tool calls, keyed by tool call id, closed on result.
    reply : cl.Message
        The reply the results panel attaches to.

    Returns
    -------
    str
        The results element name when a panel was attached, else empty.
    """
    if node == "model":
        for call in getattr(message, "tool_calls", None) or []:
            step = cl.Step(name=TOOL_STEPS.get(call["name"], call["name"]))
            await step.__aenter__()
            open_steps[call["id"]] = step
        return ""

    if node != "tools":
        return ""

    step = open_steps.pop(getattr(message, "tool_call_id", ""), None)
    if step is not None:
        await step.__aexit__(None, None, None)

    rows = _rows_from(message)
    if not rows:
        return ""

    target = "orders" if getattr(message, "name", "") == "search_orders" else "tonnage"
    reply.elements = cast(
        "list[Any]",
        [
            cl.CustomElement(
                name=RESULTS_ELEMENT,
                props=results_props(target, rows),
                display="side",
            )
        ],
    )
    return RESULTS_ELEMENT


def _rows_from(message: Any) -> list[dict[str, Any]]:
    """Read the row list back out of a tool result message.

    Tool results arrive as text, so the rows are parsed rather than passed. A
    result that is not a list of rows -- ``ask_user`` returning a form, or an
    error string -- yields nothing.

    Parameters
    ----------
    message : Any
        A tool message.

    Returns
    -------
    list of dict
        The rows, or empty when the result was not a row list.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.startswith("["):
        return []
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) and parsed else []


async def _ask_to_refine(payload: dict[str, Any]) -> dict[str, str] | None:
    """Show the refinement form and wait for the desk to fill it in.

    Parameters
    ----------
    payload : dict
        The interrupt value, carrying ``reason`` and ``fields``.

    Returns
    -------
    dict of str to str or None
        What was submitted, or None if it timed out or was cancelled.
    """
    answer = await cl.AskElementMessage(
        content=payload.get("reason", "Which values did you mean?"),
        element=cl.CustomElement(
            name=REFINE_ELEMENT, props={"fields": payload.get("fields", {})}
        ),
    ).send()
    if answer is None or not answer.get("submitted"):
        return None
    return {k: v for k, v in answer.items() if k not in {"submitted", "id"}}


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
    """Reattach to a persisted conversation and restore the gauge.

    Without this callback old threads are listed and readable but cannot be
    continued. Chainlit replays the stored steps itself, so there is no state to
    rebuild until the agent carries memory.

    The gauge is unpersisted, so it is rebuilt here against the last stored
    assistant message.

    Parameters
    ----------
    thread : ThreadDict
        Stored thread record supplied by the data layer, read here for the id of
        the last assistant message.

    Returns
    -------
    None
    """
    anchors = [
        str(step_id)
        for step in thread.get("steps") or []
        if step.get("type") == "assistant_message" and (step_id := step.get("id"))
    ]
    if anchors:
        await refresh_gauge(agent_history(), anchor=anchors[-1])


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
