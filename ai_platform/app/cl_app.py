"""Chainlit entry point.

The only module in this project that imports chainlit. Model and agent
behaviour belongs in ``ai_platform.backend`` so it stays runnable without a web
server.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

import chainlit as cl
from chainlit.types import ThreadDict
from langchain_core.messages import AIMessageChunk
from langgraph.types import Command

from ai_platform.app.data_layer import get_data_layer
from ai_platform.backend.agent import agent
from ai_platform.backend.context import USABLE_TOKENS, history_tokens
from ai_platform.backend.llm import stream_chat
from ai_platform.backend.logging_utils import get_logger
from ai_platform.backend.tables import OrderSearch, VesselSearch, resolve_table
from ai_platform.backend.tracing import langfuse_handler

__all__ = ["get_data_layer"]

_logger = get_logger("chat")

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
    "search_orders_and_tonnage": "Order and tonnage search",
    "ask_user": "Clarifying question",
}
"""Progress step shown while each tool runs."""

STEP_OUTPUT_CAP = 500
"""Characters of a non-search tool result shown inside its step.

Caps ``ask_user`` forms and tool errors so a stray payload cannot push the
transcript out of the viewport.
"""

ASK_ELEMENT = "AskUser"
"""Element name for the question form ``ask_user`` renders."""

ASK_TIMEOUT_SECONDS = 300
"""How long the question form waits before Chainlit gives up on an answer.

Chainlit's default is ninety seconds, sized for a yes/no. A form with several
questions is read, not typed.
"""

AGENT_DESCRIPTION = """Your supply and demand AI Agent for vessel positions and cargo enquiries.

For vessels, you can narrow by vessel id, availability dates, ETA, deadweight
tonnage, ballast or laden, and commercial status.

For cargoes, you can narrow by order id, laycan window, date received, last
updated, and cargo weight.

Just ask in plain language and I will take it from there."""

AGENT_COMMANDS = (
    ("Cargo list", "Show me today's cargo list in West Aussie", "package"),
    ("C3 iron ore", "Show me the latest C3 iron ore orders", "package"),
    ("ECSA orders", "What orders are loading in ECSA?", "package"),
    ("PDM to N China", "Show me orders from PDM to North China", "package"),
    ("Tonnage list", "Show me today's tonnage list open in the Pacific", "ship"),
    ("ECSA ballasters", "Show me the ECSA ballasters", "ship"),
    ("WAF openers", "Show me the WAF openers", "ship"),
    ("Big ships in Jan", "Vessels of at least 180,000 dwt open in January 2026", "ship"),
    ("November laycan", "Cargoes with laycan in November 2025 over 150,000 tonnes", "package"),
)
"""Starter questions offered in the composer's slash menu, as label, question, icon.

The label is what the picker lists and what comes back on ``Message.command``;
the question is what is actually asked. Icons are lucide names.
"""

STARTER_QUESTIONS = {label: question for label, question, _ in AGENT_COMMANDS}
"""Command label to the question it stands for."""

AGENT_STARTERS = [
    cl.Starter(label=label, message=question)
    for label, question, _ in AGENT_COMMANDS
]
"""The same questions as welcome-screen buttons, for an empty thread."""

_INVISIBLE = str.maketrans({"\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": ""})
_ODD_SPACES = str.maketrans({"\u202f": " ", "\u00a0": " "})


def _clean(text: str) -> str:
    """Strip the invisible characters a degenerating model emits.

    ``gpt-oss`` on Groq sometimes runs into thousands of zero-width spaces, and
    uses narrow no-break spaces inside ordinary names. The former render as
    nothing or as dots; the latter break copy and search. Neither carries
    meaning.

    Parameters
    ----------
    text : str
        A streamed token.

    Returns
    -------
    str
        The token with zero-width characters removed and odd spaces made plain.
    """
    return text.translate(_INVISIBLE).translate(_ODD_SPACES)


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


def results_props(target: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the props for the results table element.

    Column choice, null substitution and the word for a row all come from the
    table module, so the UI carries no knowledge of what the columns mean. Values
    arrive JSON-safe from ``fetch_rows``, so nothing is coerced here.

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
                value if (value := row.get(column)) is not None else defaults.get(column)
                for column in columns
            ]
            for row in rows
        ],
        "noun": spec.display_noun,
    }


async def refresh_gauge(
    history: list[dict[str, str]], spent: int = 0, anchor: str = ""
) -> None:
    """Create or update the bar pinned above the composer.

    An element only renders inside the message whose id equals its ``for_id``;
    the frontend rejects an empty one outright, so the bar has to be anchored to
    a real message even though it is drawn fixed to the viewport. The anchor is
    therefore the first reply of the session — nothing is sent before that,
    because a message on chat start would replace the welcome screen. Once set
    it is reused, which is what keeps a single bar rather than one per reply
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
    used = history_tokens(history)
    props = {
        "percent": round(min(used / USABLE_TOKENS, 1.0) * 100, 2),
        "used": used,
        "usable": USABLE_TOKENS,
        "spent": spent,
    }
    stored = cl.user_session.get(GAUGE_SESSION_KEY)
    if stored is None:
        if not anchor:
            return
        gauge = cl.CustomElement(name="ComposerBar", props=props, for_id=anchor)
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
    config = {
        "configurable": {"thread_id": cl.context.session.id},
        "callbacks": [langfuse_handler],
    }
    payload: Any = {"messages": [{"role": "user", "content": question}]}
    reply = root_message()
    results_element_name = ""

    while True:
        interrupt_value: dict[str, Any] | None = None
        open_steps: dict[str, cl.Step] = {}

        stream = agent.astream(
            payload, cast("Any", config), stream_mode=["updates", "messages"]
        )
        failed = False
        try:
            async for mode, event in stream:
                if mode == "messages":
                    chunk, meta = cast("tuple[Any, dict[str, Any]]", event)
                    text = _clean(chunk.content) if isinstance(chunk.content, str) else ""
                    is_answer_token = (
                        meta.get("langgraph_node") == "model"
                        and isinstance(chunk, AIMessageChunk)
                        and not chunk.tool_call_chunks
                    )
                    if is_answer_token and text:
                        await reply.stream_token(text)
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
        except Exception as error:
            _logger.exception("agent run failed")
            failed = True
            await reply.stream_token(
                f"\n\nSomething went wrong on my side and I could not finish: {error}"
            )

        for step in open_steps.values():
            await step.__aexit__(None, None, None)

        if failed or interrupt_value is None:
            break

        submitted = await _ask_user(interrupt_value)
        if submitted is None:
            await reply.stream_token(
                "\n\nNo problem — ask again whenever you have those details."
            )
            break
        payload = Command(resume=submitted)

    if results_element_name:
        await reply.stream_token(f"\n\n{results_element_name}")
    await reply.send()
    await refresh_gauge(agent_history(), anchor=reply.id)


async def _render_node_message(
    message: Any, node: str, open_steps: dict[str, cl.Step], reply: cl.Message
) -> str:
    """Show one message from the agent as a progress step or a results panel.

    A search result is attached to the reply, which is what persists it and gives
    the reply its link, and pushed straight into the sidebar under a fresh key, so
    the panel swaps to the newest rows rather than keeping the last set open.

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
            step = cl.Step(
                name=TOOL_STEPS.get(call["name"], call["name"]),
                type="tool",
                show_input=True,
            )
            step.input = _step_input(call.get("args", {}))
            await step.__aenter__()
            open_steps[call["id"]] = step
        return ""

    if node != "tools":
        return ""

    sets = _sets_from(message)
    step = open_steps.pop(getattr(message, "tool_call_id", ""), None)
    if step is not None:
        step.output = _step_output(message, sets)
        await step.__aexit__(None, None, None)

    if not sets:
        return ""

    panel = cl.CustomElement(name=RESULTS_ELEMENT, props={"sets": sets}, display="side")
    reply.elements = cast("list[Any]", [panel])
    await cl.ElementSidebar.set_title(RESULTS_ELEMENT)
    await cl.ElementSidebar.set_elements(
        [panel], key=getattr(message, "tool_call_id", "") or panel.id
    )
    return RESULTS_ELEMENT


_SEARCH_MODELS = {"cargoes": OrderSearch, "vessels": VesselSearch}
"""Search model per table, read for the meaning column of a tool step."""


_BOUND_SUFFIXES = (("_from", "_to"), ("_min", "_max"))

_BOUND_WORDING = re.compile(
    r",?\s*(on or after|on or before|at or above this|at or below this|"
    r"lower bound|upper bound).*$"
)


def _paired_rows(
    values: dict[str, Any], fields: dict[str, Any]
) -> list[tuple[str, str, str]]:
    """Collapse each ``_from``/``_to`` or ``_min``/``_max`` pair into one range row.

    ``open_end_from = 3 Sep`` and ``open_end_to = 3 Oct`` are two bounds on one
    date, so they read as ``open end | 2025-09-03 → 2025-10-03`` rather than as
    two rows that look like a start and an end.

    Parameters
    ----------
    values : dict
        The fields the model set for one table.
    fields : dict
        That table's Pydantic fields, for the meaning column.

    Returns
    -------
    list of tuple
        ``(label, value, meaning)`` per row, in the order the fields appeared.
    """
    set_values = {k: v for k, v in values.items() if v not in (None, "", [], False)}
    rows: list[tuple[str, str, str]] = []
    consumed: set[str] = set()

    def meaning_of(field: str) -> str:
        return (fields[field].description or "") if field in fields else ""

    for field, value in set_values.items():
        if field in consumed:
            continue
        for low, high in _BOUND_SUFFIXES:
            base = field[: -len(low)] if field.endswith(low) else None
            base = base or (field[: -len(high)] if field.endswith(high) else None)
            if base is None:
                continue
            lower, upper = set_values.get(base + low), set_values.get(base + high)
            consumed.update({base + low, base + high})
            is_date = low == "_from"
            if lower is not None and upper is not None:
                shown = f"{lower} → {upper}"
            elif lower is not None:
                shown = f"{'on or after' if is_date else 'at least'} {lower}"
            else:
                shown = f"{'on or before' if is_date else 'at most'} {upper}"
            source = base + low if base + low in fields else base + high
            rows.append((base.replace("_", " "), shown, _BOUND_WORDING.sub("", meaning_of(source))))
            break
        else:
            rows.append((field.replace("_", " "), str(value), meaning_of(field)))
    return _merge_overlap(rows)


def _merge_overlap(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Fold an overlap pair into one ``during`` row.

    "Closes on or after A" together with "opens on or before B" is the single
    question "open at some point between A and B", so the step says that.

    Parameters
    ----------
    rows : list of tuple
        Rows from :func:`_paired_rows`.

    Returns
    -------
    list of tuple
        The same rows with each overlap pair replaced by one range row.
    """
    for window in ("open", "laycan"):
        closes = next((r for r in rows if r[0] == f"{window} end" and r[1].startswith("on or after ")), None)
        opens = next((r for r in rows if r[0] == f"{window} start" and r[1].startswith("on or before ")), None)
        if closes and opens:
            start = closes[1].removeprefix("on or after ")
            end = opens[1].removeprefix("on or before ")
            merged = (f"{window} during", f"{start} → {end}", f"{window} window overlaps this range")
            rows = [merged if r is closes else r for r in rows if r is not opens]
    return rows


def _step_input(args: dict[str, Any]) -> str:
    """Render tool arguments as a markdown table.

    Only the fields the model actually set are listed. A search sends a model
    with every column present and almost all of them null, so dumping the raw
    arguments buries the four values that matter in forty that do not. The
    meaning column is each field's own description, so a step reads without the
    schema open beside it.

    Parameters
    ----------
    args : dict
        Arguments from the tool call: one nested dict per table for a search,
        a list of questions for ``ask_user``, flat values for anything else.

    Returns
    -------
    str
        Markdown table of table, field, value and meaning for a search; one
        bullet per question for ``ask_user``; empty when nothing was set.
        Rendered as markdown only while the step's ``show_input`` stays boolean;
        a string there is treated as a code-block language and fences it.
    """
    lines: list[str] = []
    questions: list[str] = []
    for group, value in args.items():
        if isinstance(value, dict):
            fields = _SEARCH_MODELS[group].model_fields if group in _SEARCH_MODELS else {}
            lines += [
                f"| {group} | {label} | {shown} | {meaning} |"
                for label, shown, meaning in _paired_rows(value, fields)
            ]
        elif isinstance(value, list):
            questions += [
                f"- **{item.get('header', '')}** — {item.get('question', '')}"
                for item in value
                if isinstance(item, dict)
            ]
        elif value not in (None, "", False):
            lines.append(f"| | {group.replace('_', ' ')} | {value} | |")

    if questions and not lines:
        return "\n".join(questions)
    if not lines:
        return ""
    return "\n".join(
        ["| table | field | value | meaning |", "| --- | --- | --- | --- |", *lines]
    )


def _step_output(message: Any, sets: list[dict[str, Any]]) -> str:
    """Summarise a tool result for the body of its progress step.

    Parameters
    ----------
    message : Any
        A tool message.
    sets : list of dict
        Props sets parsed from it, empty for a non-search tool.

    Returns
    -------
    str
        Row counts for a search; one ``question: answer`` bullet per entry for
        a form the user submitted; otherwise the raw result capped at
        ``STEP_OUTPUT_CAP``.
    """
    if sets:
        return ", ".join(f"{len(one['rows'])} {one['noun']}" for one in sets)
    content = str(getattr(message, "content", ""))
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and "counts" in parsed:
        return "no rows"
    if isinstance(parsed, dict) and parsed and all(
        isinstance(value, str) for value in parsed.values()
    ):
        return "\n".join(f"- {question}: **{answer}**" for question, answer in parsed.items())
    return content[:STEP_OUTPUT_CAP] if content else "no rows"


def _sets_from(message: Any) -> list[dict[str, Any]]:
    """Read the searched tables back out of a tool result message.

    Tool results arrive as text, so the rows are parsed rather than passed. The
    search tool returns one key per table it was asked for; a table it skipped
    is present but empty, and drops out here rather than becoming a blank tab.

    Parameters
    ----------
    message : Any
        A tool message.

    Returns
    -------
    list of dict
        One props set per non-empty table, in the order the tabs appear. Empty
        when the result was not a search -- ``ask_user`` returning a form, or an
        error string.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.startswith("{"):
        return []
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    return [
        results_props(target, rows)
        for key, target in (("cargoes", "orders"), ("vessels", "tonnage"))
        if isinstance(rows := parsed.get(key), list) and rows
    ]


async def _ask_user(payload: dict[str, Any]) -> dict[str, str] | None:
    """Show the question form and wait for the user to answer it.

    Parameters
    ----------
    payload : dict
        The interrupt value, carrying ``questions``.

    Returns
    -------
    dict of str to str or None
        Question text to the answer chosen or typed, or None if the form timed
        out or was cancelled.
    """
    answer = await cl.AskElementMessage(
        content="Before I search:",
        element=cl.CustomElement(name=ASK_ELEMENT, props=cast("dict[str, Any]", payload)),
        timeout=ASK_TIMEOUT_SECONDS,
    ).send()
    if answer is None or not answer.get("submitted"):
        return None
    return {
        str(key): str(value)
        for key, value in answer.items()
        if key not in {"submitted", "id"}
    }


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




@cl.on_chat_start
async def start_chat() -> None:
    """Register the starter commands in the composer's slash menu.

    Nothing is sent here. A message on chat start would replace the welcome
    screen, and the gauge is not worth that — it waits for the first reply to
    anchor to instead.

    The same questions appear twice by design: as welcome-screen buttons, which
    Chainlit renders only while the thread is empty, and as commands, which stay
    reachable for the rest of the conversation.

    Returns
    -------
    None
    """
    if cl.user_session.get("chat_profile") == PLAIN_MODEL_PROFILE:
        return

    await cl.context.emitter.set_commands(
        [
            {
                "id": label,
                "description": question,
                "icon": icon,
                "button": False,
                "persistent": False,
                "selected": False,
            }
            for label, question, icon in AGENT_COMMANDS
        ]
    )


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

    question = message.content.strip() or STARTER_QUESTIONS.get(
        message.command or "", ""
    )
    if question != message.content:
        message.content = question
        await message.update()

    await run_agent(question)
