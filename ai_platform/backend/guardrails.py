"""What a reply rests on, and the footer that tells the trader.

The agent's prose can claim anything; the tool calls behind it cannot. This
records what each run actually searched and got back, and turns that into the
sources line and confidence level appended under the reply. Both are decided
here, from evidence, never by the model, so a reply cannot talk itself up.

Plain values in, plain values out: ``cl_app`` feeds it the tool calls and
results it already sees, which keeps this importable without Chainlit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ai_platform.backend import tables

SEARCH_TABLES = (("cargoes", tables.ORDERS), ("vessels", tables.TONNAGE))
"""The search tool's table keys and the spec behind each."""

SOURCE_NAMES = {
    "cargoes": "cargo enquiries",
    "vessels": "vessel positions",
    "new vessels": "new vessels feed",
    "matches": "vessel matches",
}
"""What each kind of evidence is called in the sources line."""

TOOL_FAILURE_PREFIX = "That search failed"
"""How ``agent._describe_tool_failure`` opens the message that replaces a result."""

AWAITING_INPUT_PHRASE = "need the trader's input"
"""Wording ``matching`` uses when some matched vessels cannot be ranked yet."""

NO_SEARCH_DISCLAIMER = (
    "\n\n_Not checked against the desk's data: no search was run for this reply._"
)
"""Footer for a reply that rests on no search at all."""


@dataclass
class Evidence:
    """What one question's run searched, and what came back.

    Attributes
    ----------
    found : dict of str to int
        Rows found per kind of evidence, keyed as in ``SOURCE_NAMES``. A key is
        present once that kind was searched, even when it found nothing.
    by_meaning : bool
        A search matched on wording rather than on names.
    capped : bool
        A search returned only its closest rows, not every match.
    awaiting_input : bool
        Some matched vessels still need the trader's input before ranking.
    errors : int
        Tool calls that failed.
    flagged : bool
        The question was passed to the dev team as unanswerable.
    out_of_range : bool
        A new vessels window reached back further than is covered.
    """

    found: dict[str, int] = field(default_factory=dict)
    by_meaning: bool = False
    capped: bool = False
    awaiting_input: bool = False
    errors: int = 0
    flagged: bool = False
    out_of_range: bool = False
    _calls: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict, repr=False)

    def record_call(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        """Note a tool call the model made, so its result can be read in context.

        Parameters
        ----------
        call_id : str
            The tool call's id, which its result carries back.
        name : str
            Tool name.
        args : dict
            Arguments the model sent.
        """
        self._calls[call_id] = (name, args or {})
        if name != "search_orders_and_tonnage":
            return
        for key, spec in SEARCH_TABLES:
            search = args.get(key) if isinstance(args, dict) else None
            if isinstance(search, dict) and any(search.get(c) for c in spec.semantic_columns):
                self.by_meaning = True

    def record_result(self, call_id: str, content: Any, status: str | None = None) -> None:
        """Read one tool result into the evidence.

        Parameters
        ----------
        call_id : str
            The tool call this result answers.
        content : Any
            The tool message's content: JSON text for a result, plain text for
            a failure.
        status : str, optional
            The tool message's status; "error" when the call failed.
        """
        name, args = self._calls.get(call_id, ("", {}))
        text = content if isinstance(content, str) else ""
        if status == "error" or text.startswith(TOOL_FAILURE_PREFIX):
            self.errors += 1
            return
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(parsed, dict):
            return

        if name == "search_orders_and_tonnage":
            counts = parsed.get("counts") or {}
            for key, _ in SEARCH_TABLES:
                if args.get(key) is not None:
                    self._add(key, counts.get(key, 0))
            self.capped = self.capped or bool(parsed.get("capped"))
        elif name == "new_vessels":
            if "out_of_range" in parsed:
                self.out_of_range = True
            else:
                self._add("new vessels", (parsed.get("counts") or {}).get("vessels", 0))
        elif name == "match_orders":
            orders = parsed.get("orders") or []
            self._add("matches", sum(order.get("matched", 0) for order in orders))
            self.awaiting_input = self.awaiting_input or any(
                AWAITING_INPUT_PHRASE in str(order.get("report", "")) for order in orders
            )
        elif name == "flag_for_dev_team":
            self.flagged = True

    def _add(self, kind: str, count: int) -> None:
        self.found[kind] = self.found.get(kind, 0) + int(count or 0)


def confidence(evidence: Evidence, cut_off: bool = False) -> tuple[str, str] | None:
    """Rate how far the reply can be trusted, from what was actually found.

    Parameters
    ----------
    evidence : Evidence
        The run's evidence.
    cut_off : bool
        The reply ended at the model's output limit.

    Returns
    -------
    tuple of str or None
        Level (High, Medium or Low) and the reason in the desk's words; None
        when nothing was searched or the question was declined, since there is
        no answer to rate.
    """
    if evidence.flagged or evidence.out_of_range:
        return None
    if not evidence.found and not evidence.errors:
        return None
    if evidence.errors:
        return "Low", "a search failed, so this may be incomplete"
    if cut_off:
        return "Low", "the reply was cut short; the results panel has every row"
    if not any(evidence.found.values()):
        return "Low", "nothing on the book matched"
    if not all(evidence.found.values()):
        return "Medium", "part of the question found nothing on the book"
    if evidence.by_meaning or evidence.capped:
        return "Medium", "cargoes found by closest wording, not an exact match"
    if evidence.awaiting_input:
        return "Medium", "some vessels need your input before they are ranked"
    return "High", "exact match on the book; every row is in the results panel"


def footer(evidence: Evidence, as_of: date, cut_off: bool = False) -> str:
    """The sources and confidence lines appended under a reply.

    Parameters
    ----------
    evidence : Evidence
        The run's evidence.
    as_of : date
        The working date the search ran against.
    cut_off : bool
        The reply ended at the model's output limit.

    Returns
    -------
    str
        Markdown to append: sources and confidence after a search; a disclaimer
        when the reply rests on no search; empty when the question was declined
        and flagged, since the reply already says so.
    """
    if evidence.flagged or evidence.out_of_range:
        return ""
    if not evidence.found and not evidence.errors:
        return NO_SEARCH_DISCLAIMER
    sources = " · ".join(
        f"{SOURCE_NAMES.get(kind, kind)} ({count:,})" for kind, count in evidence.found.items()
    )
    lines = [f"Sources: {sources or 'none'}, as of {as_of:%d %B %Y}."]
    rated = confidence(evidence, cut_off)
    if rated is not None:
        level, reason = rated
        lines.append(f"Confidence: {level} — {reason}.")
    return "\n\n" + "  \n".join(f"_{line}_" for line in lines)
